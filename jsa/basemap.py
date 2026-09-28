"""The ground under the live map: land, water, towns and roads (n23).

Drawn from shipped public-domain data in data/ (built by
tools/build_map_data.py), never from a tile server: a tile request is a
report of where the operator is looking, and where they are looking is where
they live (ADR 0014, 0015). Nothing here reaches the network.

It lines up with the pins by construction, not by care: every vertex goes
through `mapview.projector(origin)`, the same function that places every pin,
and the result is in the same miles the page's map transform already scales.
How closely it lines up with the real world is a property of the data and is
measured in ADR 0019; `TIERS` in the build tool says how far each tier may be
zoomed before that would show.

The basemap never decides anything. Inside or outside the radius is still
`d` from places.nearest; a pin is never moved, hidden or counted by it.
"""

from __future__ import annotations

import gzip
import json
import math
from array import array
from functools import lru_cache

from . import mapview, places
from .config import ROOT

DATA = ROOT / "data"
TIERS = ("coarse", "medium", "fine")

# How far around the requested point each tier is cut, in miles. Wide enough
# for the whole view at the smallest scale the tier is used at, with room to
# pan before the page asks for more: fine is used from 4 px/mi, where a
# 1,400 x 720 map is 200 miles corner to centre. Coarse is the whole country.
REACH = {"coarse": None, "medium": 1300.0, "fine": 300.0}

# Decimal places of a mile in the paths: rounding stays a tenth of the tier's
# own tolerance (0.625, 0.125, 0.025 mi).
DECIMALS = {"coarse": 1, "medium": 2, "fine": 3}

# The largest scale, in pixels per mile, each tier is drawn at. The build
# tool (tools/build_map_data.py TIERS) simplified each file for exactly this;
# a test holds the two, and the shipped files, to the same numbers.
PPM = {"coarse": 0.8, "medium": 4.0, "fine": 20.0}

# The order the page paints them in, bottom to top.
LAYERS = ("land", "urban", "county", "lake", "road")


def path(tier: str):
    return DATA / f"us_basemap_{tier}.json.gz"


def available() -> bool:
    return all(path(t).exists() for t in TIERS)


@lru_cache(maxsize=len(TIERS))
def _tier(tier: str) -> dict | None:
    """The tier as shipped, or None when its file is missing."""
    file = path(tier)
    if not file.exists():
        return None
    with gzip.open(file, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    # Held as 4-byte integers, not Python ints: the fine tier is 87 MB as
    # lists and 29 MB as arrays (measured), for the life of the server.
    for features in data["layers"].values():
        for feature in features:
            feature[4] = array("i", feature[4])
    return data


def warm() -> None:
    """Load every tier now, so the first zoom does not wait for the fine
    one (about 2 s to read). Run in the background when the server starts."""
    for tier in TIERS:
        _tier(tier)


def _box(centre: tuple[float, float], reach: float | None):
    """(west, south, east, north) in degrees around (lat, lon), or None for
    everything. Longitude is widened for the latitude furthest from the
    equator, so the box never comes up short."""
    if reach is None:
        return None
    lat, lon = centre
    dlat = reach / 69.05
    far = min(89.0, abs(lat) + dlat)
    dlon = min(180.0, reach / (69.17 * math.cos(math.radians(far))))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def _meets(feature, box, q: int) -> bool:
    west, south, east, north = box
    return not (feature[2] < west * q or feature[0] > east * q
                or feature[3] < south * q or feature[1] > north * q)


def _svg_path(features, q: int, project, decimals: int, closed: bool) -> str:
    """One SVG path for a whole layer: 'M x y l dx dy ...', with the deltas
    taken between ROUNDED points so rounding never accumulates along a line.
    y is north-up; the page's transform flips it, as it does for the pins."""
    scale = 10 ** decimals
    out: list[str] = []
    for feature in features:
        flat = feature[4]
        ix = iy = 0
        prev = None
        parts: list[str] = []
        for i in range(0, len(flat), 2):
            ix += flat[i]
            iy += flat[i + 1]
            x, y = project(iy / q, ix / q)
            here = (round(x * scale), round(y * scale))
            if prev is None:
                parts.append(f"M{here[0] / scale:g} {here[1] / scale:g}l")
            elif here != prev:
                parts.append(f"{(here[0] - prev[0]) / scale:g} "
                             f"{(here[1] - prev[1]) / scale:g}")
            prev = here
        if len(parts) > 1:
            out.append(parts[0] + " ".join(parts[1:]) + ("z" if closed else ""))
    return "".join(out)


def tier_for(per_mile: float) -> str:
    """The coarsest tier still honest at this scale."""
    return next((t for t in TIERS if per_mile <= PPM[t]), TIERS[-1])


def layers(origin: places.Place, tier: str, at: tuple[float, float] = (0.0, 0.0),
           reach: float | None = None) -> dict | None:
    """The tier around the point `at` (miles from origin, as the page's map
    measures), projected about `origin`, as one SVG path per layer. `reach`
    overrides how far around `at` to cut (the static map needs only its
    frame).

    Returns None when the data is missing: the caller draws the map it drew
    before n23, which is not a failure worth an exception.
    """
    if tier not in TIERS:
        raise ValueError(tier)
    reach = reach if reach is not None else REACH[tier]
    # Snap the centre, so panning a few miles reuses what was just built.
    step = (reach or 1.0) / 4
    snapped = (round(at[0] / step) * step, round(at[1] / step) * step) if reach else (0.0, 0.0)
    return _layers(origin, tier, snapped, reach)


@lru_cache(maxsize=24)
def _layers(origin: places.Place, tier: str, at: tuple[float, float],
            reach: float | None) -> dict | None:
    data = _tier(tier)
    if data is None:
        return None
    q = data["q"]
    box = _box(mapview.unproject(origin, *at), reach)
    project = mapview.projector(origin)
    out = {}
    for name in LAYERS:
        features = data["layers"].get(name)
        if features is None:
            continue
        if box is not None:
            features = [f for f in features if _meets(f, box, q)]
        out[name] = _svg_path(features, q, project, DECIMALS[tier],
                              closed=name != "road")
    return {"tier": tier, "ppm": data["ppm"], "reach": reach,
            "at": list(at), "layers": out}
