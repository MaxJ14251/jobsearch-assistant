"""Where a posting actually is, in miles from where you actually are.

Location was string matching: a `regions` block listing towns by hand, a
city-substring test, a state test. It took three goals to work outside
California and it still cannot answer "is this an hour away".

This places a posting on the map instead. The data ships with the tool --
Census Gazetteer centroids, public domain, 32,109 towns and 33,791 ZIP codes
in about 690 KB -- so nothing here calls a geocoding service. A geocoder
would need a key, and it would send every posting's location and the
operator's own home ZIP to somebody else. A home ZIP is identity.

What it will NOT do is guess. A posting placed in the wrong town is worse
than one left unplaced, because the operator cannot see that it happened:
"Bellevue" is a real city in Washington and a real city in Nebraska, so a
bare "Bellevue" resolves to neither. Everything it declines to place is
counted and reported rather than dropped.
"""

from __future__ import annotations

import csv
import gzip
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .config import ROOT

DATA = ROOT / "data"
EARTH_MILES = 3958.7613

STATE_CODES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR",
}
CODES = set(STATE_CODES.values())

# Names a posting uses that the Census file does not. Each one is a place
# the Gazetteer knows under a different name, not a guess about a region.
ALIASES = {
    "nyc": ("New York", "NY"),
    "new york city": ("New York", "NY"),
    "washington dc": ("Washington", "DC"),
    "washington d.c.": ("Washington", "DC"),
    "washington, d.c.": ("Washington", "DC"),
    "d.c.": ("Washington", "DC"),
}

# "Remote", "Flexible", "Anywhere" -- a place-shaped word that is not a place.
REMOTE = re.compile(
    r"\b(remote|flexible|anywhere|work from home|wfh|virtual|telecommute)\b", re.I)

# Country noise around the real text: "US - Example Town, United States".
COUNTRY = re.compile(
    r"^(us|usa|u\.s\.|u\.s\.a\.|united states( of america)?)\s*[-–—,]\s*|"
    r"\s*[-–—,]\s*(usa|u\.s\.a\.|united states( of america)?)$", re.I)

SPLIT = re.compile(r"\s*(?:;|\||/(?!\s*\d)| or )\s*", re.I)

# "1 Market St", "110 110th Ave NE": a street address, not a place.
ADDRESS = re.compile(r"^\d+\s+\S")


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float

    def __str__(self) -> str:
        return f"{self.name}, {self.state}"


@dataclass
class Placement:
    """Everything one location string turned out to mean."""

    places: list[Place] = field(default_factory=list)
    remote: bool = False
    unplaced: list[str] = field(default_factory=list)

    @property
    def placed(self) -> bool:
        return bool(self.places)


@lru_cache(maxsize=1)
def _places() -> tuple[dict[tuple[str, str], Place], dict[str, list[Place]]]:
    """(by name+state, by name). Loaded once, from the shipped file."""
    by_key: dict[tuple[str, str], Place] = {}
    by_name: dict[str, list[Place]] = {}
    path = DATA / "us_places.csv.gz"
    if not path.exists():
        return by_key, by_name
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            place = Place(row["name"], row["state"],
                          float(row["lat"]), float(row["lon"]))
            key = place.name.lower()
            by_key[(key, place.state)] = place
            by_name.setdefault(key, []).append(place)
            # The Census calls Idaho's capital "Boise City"; everybody else
            # calls it Boise. Index the shorter form too -- but never when
            # that form is a STATE name, which would turn a posting reading
            # "Oklahoma" into Oklahoma City and "Kansas" into Kansas City.
            short = re.sub(r"\s+city$", "", key)
            if short != key and short not in STATE_CODES:
                by_key.setdefault((short, place.state), place)
                by_name.setdefault(short, []).append(place)
    return by_key, by_name


@lru_cache(maxsize=1)
def _zips() -> dict[str, Place]:
    out: dict[str, Place] = {}
    path = DATA / "us_zips.csv.gz"
    if not path.exists():
        return out
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["zip"]] = Place(row["zip"], "", float(row["lat"]),
                                    float(row["lon"]))
    return out


def data_is_present() -> bool:
    """False when the shipped data is missing, so callers can say so."""
    return bool(_places()[0]) and bool(_zips())


def miles(a: Place, b: Place) -> float:
    """Great-circle distance. Good to a fraction of a mile at these ranges."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_MILES * math.asin(math.sqrt(h))


def resolve(name: str, state: str | None = None) -> Place | None:
    """A town, or None when the name does not identify exactly one.

    With a state it is a lookup. Without one it resolves only if the name is
    unique in the whole country -- "Seattle" is, "Bellevue" is not, and
    guessing the bigger Bellevue would put a job 1,600 miles from where the
    operator thinks it is.
    """
    by_key, by_name = _places()
    key = (name or "").strip().lower()
    if not key:
        return None
    # A bare state name is a state. Pennsylvania has a borough called
    # Oklahoma, and placing a posting that said "Oklahoma" there would be
    # 1,100 miles wrong and invisible.
    if state is None and key in STATE_CODES:
        return None
    if key in ALIASES:
        alias_name, alias_state = ALIASES[key]
        name, key = alias_name, alias_name.lower()
        state = state or alias_state
    if state:
        state = state.strip().upper()
        if state not in CODES:
            state = STATE_CODES.get(state.lower(), "")
        return by_key.get((key, state)) if state else None
    matches = by_name.get(key) or []
    return matches[0] if len(matches) == 1 else None


def from_zip(code: str) -> Place | None:
    return _zips().get((code or "").strip()[:5])


def origin(text: str) -> Place | None:
    """Where the operator is: a ZIP, or a "City, ST" / "City, State"."""
    text = (text or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{5}(-\d{4})?", text):
        return from_zip(text)
    return _one(text)


def _state_of(token: str) -> str | None:
    token = token.strip().rstrip(".")
    if token.upper() in CODES:
        return token.upper()
    return STATE_CODES.get(token.lower())


def _one(part: str) -> Place | None:
    """One comma-separated fragment: "Los Angeles, California", "Bellevue - 1 Main"."""
    part = COUNTRY.sub("", part).strip(" ,-–—")
    if not part:
        return None
    # "Example Town - 1 Market St": the street address is not a
    # place, so the head is. But "England - Cambridge" is two place names,
    # and taking its head put a Cambridge (UK) posting in England, Arkansas.
    # A tail only counts as an address when it reads like one.
    head = part
    pieces = re.split(r"\s+[-–—]\s+", part)
    if len(pieces) == 2 and ADDRESS.match(pieces[1].strip()):
        head = pieces[0].strip()
    # "NYC (SoHo)", "Boise (HQ)": the parenthetical is a note, not a place.
    head_no_paren = re.sub(r"\s*\(.*?\)\s*", " ", head).strip()

    bits = [b.strip() for b in re.split(r",", head_no_paren) if b.strip()]
    if not bits:
        return None
    if len(bits) >= 2:
        state = _state_of(bits[-1])
        if state:
            return resolve(", ".join(bits[:-1]), state)
        # "Long Beach, California, United States" already had its country
        # stripped; anything else with three parts is not something to guess at.
        state = _state_of(bits[-2]) if len(bits) >= 3 else None
        if state:
            return resolve(", ".join(bits[:-2]), state)
        return None
    return resolve(bits[0])


def parse(text: str) -> Placement:
    """Everything one posting's location field means: places, remote, neither."""
    out = Placement()
    text = (text or "").strip()
    if not text:
        return out
    for part in SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        if REMOTE.search(part):
            out.remote = True
            # "Example Town, CA (Remote)" still names a place; "US Remote" does not.
            stripped = REMOTE.sub("", part).strip(" ,-–—()")
            place = _one(stripped) if stripped else None
            if place and place not in out.places:
                out.places.append(place)
            continue
        place = _one(part)
        if place is None:
            out.unplaced.append(part)
        elif place not in out.places:
            out.places.append(place)
    return out


def nearest(home: Place, placement: Placement | str) -> float | None:
    """Miles to the closest place on the posting, or None if it has none."""
    if isinstance(placement, str):
        placement = parse(placement)
    if not placement.places or home is None:
        return None
    return min(miles(home, place) for place in placement.places)


def within(home: Place, radius_miles: float, locations: Iterable[str]) -> int:
    """How many of these locations fall inside the radius. For reporting."""
    count = 0
    for text in locations:
        distance = nearest(home, text)
        if distance is not None and distance <= radius_miles:
            count += 1
    return count
