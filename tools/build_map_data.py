"""Rebuild the shipped data files in data/ from their public sources.

`data/` holds 7.5 MB of compressed binary that decides where every job
posting is placed on a map, and what ground is drawn under it. Until this
script existed there was no way for anybody reading the repository to check
it: the files had to be taken on trust, which is not a thing to ask of
somebody cloning a job-search tool.

Run it and diff:

    python tools/build_map_data.py --out data --cache <somewhere>

It fetches the public-domain sources below, rebuilds every shipped file, and
prints the count and SHA-256 of each. The gzip member is written with
mtime=0 so the bytes are reproducible: the same sources give the same hash on
any machine, any day. --cache keeps the downloads (about 100 MB) so a second
run fetches nothing.

This is a BUILD tool. Nothing in jsa/ imports it, and it is the only file in
the project allowed to open a socket for reference data -- tests/test_map.py
asserts that the drawing path cannot.

Sources. US Census Bureau files are public domain (17 USC 105); Natural
Earth is dedicated to the public domain by its makers.
  2024 Gazetteer places,
  2023 place bounds 1:500k   -> data/us_places.csv.gz   (town points; see OFFSHORE_MILES)
  2024 Gazetteer ZCTAs       -> data/us_zips.csv.gz     (ZIP centroids)
  2023 states 1:20m          -> data/us_outline.json.gz (the national map's outline)
  2023 states and counties
  1:500k, 2020 urban areas
  1:500k, 2024 TIGER primary
  roads, Natural Earth 10m
  lakes and populated places,
  2024 TIGER area water for
  the counties those lakes
  touch                      -> data/us_basemap_{coarse,medium,fine}.json.gz
                                (the ground under the live map, n23, ADR 0019)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import struct
import sys
import urllib.request
import zipfile
from pathlib import Path

GAZ = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
       "2024_Gazetteer/2024_Gaz_{}_national.zip")
PLACES_URL = GAZ.format("place")
ZIPS_URL = GAZ.format("zcta")
OUTLINE_URL = ("https://www2.census.gov/geo/tiger/GENZ2023/shp/"
               "cb_2023_us_state_20m.zip")

# How much of the outline to keep. 0.05 degrees is about 3.5 miles, which is
# under one pixel on a map of the whole country -- the only scale this outline
# is ever drawn at (ADR 0015). Measured alternatives, all 52 shapes:
#   raw            13,698 points  83 KB gzipped
#   0.05 deg        2,717 points  14 KB gzipped   <- this
#   0.15 deg        1,046 points   4 KB gzipped   visibly angular
OUTLINE_TOLERANCE = 0.05
OUTLINE_DECIMALS = 3

# The basemap under the live map (n23, ADR 0019). All public domain: the
# Census files under 17 USC 105, Natural Earth by its own dedication.
GENZ = "https://www2.census.gov/geo/tiger/GENZ{0}/shp/cb_{0}_us_{1}.zip"
BASEMAP_URLS = {
    "state": GENZ.format(2023, "state_500k"),       # land: clipped to the shore
    "county": GENZ.format(2023, "county_500k"),
    "urban": GENZ.format(2020, "ua20_corrected_500k"),
    "roads": ("https://www2.census.gov/geo/tiger/TIGER2024/PRIMARYROADS/"
              "tl_2024_us_primaryroads.zip"),
    "lakes": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_lakes.zip",
    "lakes_na": ("https://naciscdn.org/naturalearth/10m/physical/"
                 "ne_10m_lakes_north_america.zip"),
}
PLACE_BOUNDS_URL = GENZ.format(2023, "place_500k")

# Lakes at the fine tier. Natural Earth's are 1:10,000,000, honest only to
# the medium tier, so up close the SAME lakes are drawn from the Census's
# own water areas, which are county by county: 3,235 files, 1.1 GB for the
# country. Only the counties that overlap a Natural Earth lake are fetched,
# and only the Census lakes and reservoirs that lie in one are kept, so a
# lake sharpens as you zoom in rather than appearing or vanishing.
AREAWATER_URL = ("https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/"
                 "tl_2024_{}_areawater.zip")
LAKE_MTFCC = {"H2030", "H2040"}         # lake or pond; reservoir
LAKE_MATCH_MILES = 0.5                  # Natural Earth's shore is this far off
# A pond beside a lake is not the lake. Measured: without this, 8,518 lake
# outlines (1.1 MB) came back, mostly ponds near a shore.
LAKE_MIN_SQ_MI = 0.25
# A piece named like a matched lake counts as that lake within this reach.
LAKE_SAME_NAME_MILES = 10.0
NOT_LAKE_MTFCC = {"H2053", "H2081"}     # ocean or sea; glacier
RIVER_MTFCC = {"H3010", "H3020"}        # parts of a lake filed as stream or canal

# City names on the map, from Natural Earth's populated places (public
# domain), which carry a population where the Census Gazetteer does not
# (ADR 0015 refused labels for that reason). Only the name, state and
# population ship; the page puts each name on the Gazetteer point of the
# same town, so a name and that town's pin cannot disagree.
CITIES_URL = ("https://naciscdn.org/naturalearth/10m/cultural/"
              "ne_10m_populated_places_simple.zip")
CITY_MIN_POP = {"coarse": 500_000, "medium": 150_000, "fine": 50_000}
# Natural Earth spellings that differ from the Census's own (n26). Not
# here: "St. Charles, MD", which the 2024 Gazetteer has no place for at all,
# so there is no point to put its name on -- it stays off the map rather
# than borrow a neighbour's.
CITY_ALIASES = {("Barlett", "TN"): "Bartlett", ("Wilkes Barre", "PA"): "Wilkes-Barre"}
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN",
    "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
    "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR",
}

MILES_PER_DEGREE = 69.05          # of latitude; the tolerances below are in it

# One tier per band of zoom, named by the largest scale (pixels per mile) it
# is drawn at. Each is simplified to half a pixel at that scale, so nothing
# the simplification moves can be seen. FINE stops at 20 px/mi because the
# 1:500,000 coastline itself is only that good: measured against the TIGER
# coastline, its 95th-percentile error is about 0.1 mile, which is 2 px at
# 20 px/mi (ADR 0019). Past that the page fades the basemap out.
#   lakes: Natural Earth is 1:10,000,000 -- honest at regional zoom only.
#   county lines: clutter until you are looking at one metro.
#   coarse roads: the interstates only; the rest is noise at country scale.
TIERS = {
    "coarse": {"ppm": 0.8, "q": 500,
               "layers": ("land", "lake", "urban", "road")},
    "medium": {"ppm": 4.0, "q": 2000,
               "layers": ("land", "lake", "urban", "road")},
    "fine": {"ppm": 20.0, "q": 10000,
             "layers": ("land", "county", "urban", "lake", "road")},
}
COARSE_ROADS = {"I"}              # RTTYP: interstates

# A Census internal point is inside the place's area INCLUDING its water, so
# a city that owns a bay -- or, for San Francisco, the Farallon Islands --
# can have its point out at sea. Measured against the 2023 1:500,000 place
# boundaries: 70 of 32,333 points fall outside their own town, 6 by more than
# half a mile. Those six move onto their town's largest piece of land; the
# rest are within the boundary file's own error and stay where Census put
# them, so a rebuild does not move towns for nothing.
OFFSHORE_MILES = 0.5

# The Gazetteer writes the legal descriptor into the name: "Abbeville city",
# "Juneau city and borough", "Nashville-Davidson metropolitan government
# (balance)". Which descriptor is in the LSAD column, so strip the one the
# row declares and nothing else.
#
# Guessing from the name instead is how Nevada's capital became "Carson":
# "Carson City" ends in the word "city" and carries no descriptor at all.
DESCRIPTOR = {
    "21": "borough", "25": "city", "35": "metro township",
    "37": "municipality", "43": "town", "47": "village",
    "53": "city and borough", "55": "comunidad", "57": "CDP",
    "62": "zona urbana", "CG": "consolidated government",
    "CN": "corporation", "MG": "metropolitan government",
    "UC": "urban county", "UG": "unified government",
}
# LSAD 00 is "no descriptor", and it holds two different things: a plain name
# ("Carson City", "Macon-Bibb County") and the remainder of a place whose
# government was consolidated, which spells its descriptor out and ends in
# "(balance)" -- "Milford city (balance)". Only the second kind is stripped,
# and the "(balance)" is what says which kind it is.
SPELLED_OUT = (
    "consolidated government", "metropolitan government", "unified government",
    "metro government", "city and borough", "metro township", "urban county",
    "municipality", "corporation", "zona urbana", "comunidad", "township",
    "borough", "village", "city", "town", "CDP",
)
BALANCE = re.compile(r"\s*\(balance\)$")


def fetch(url: str, cache: Path | None) -> bytes:
    """The archive at `url`, from the cache if it is already there."""
    if cache is not None:
        cached = cache / url.rsplit("/", 1)[-1]
        if cached.exists():
            print(f"  cached  {cached.name}")
            return cached.read_bytes()
    print(f"  fetch   {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "jsa-build"})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / url.rsplit("/", 1)[-1]).write_bytes(data)
    return data


def _only_member(archive: bytes, suffix: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(suffix)]
        if len(names) != 1:
            raise SystemExit(f"expected one {suffix} in the archive, got {names}")
        return zf.read(names[0])


def _tsv(blob: bytes) -> list[list[str]]:
    """The Gazetteer files are tab separated, UTF-8, and right-padded."""
    lines = blob.decode("utf-8").splitlines()
    return [[cell.strip() for cell in line.split("\t")]
            for line in lines[1:] if line.strip()]


def plain_name(name: str, lsad: str) -> str:
    """"Abbeville city" -> "Abbeville". "Carson City" stays "Carson City"."""
    name = name.strip()
    remainder = BALANCE.search(name) is not None
    name = BALANCE.sub("", name).strip()
    descriptor = DESCRIPTOR.get(lsad)
    if descriptor is None and remainder:
        lowered = name.lower()
        descriptor = next((d for d in SPELLED_OUT
                           if lowered.endswith(" " + d.lower())), None)
    if descriptor and name.lower().endswith(" " + descriptor.lower()):
        name = name[: -(len(descriptor) + 1)].strip()
    return name


def build_places(archive: bytes, bounds: dict[str, list] | None = None
                 ) -> list[tuple[str, str, float, float]]:
    """name,state,lat,lon -- one row per town, biggest wins a shared name.

    With `bounds` (place_bounds()), a point more than OFFSHORE_MILES outside
    its own town is moved onto the town's land; see OFFSHORE_MILES.

    A name repeats inside a state (Arkansas has two Salems). The larger by
    land area is the one a job posting means often enough that guessing the
    smaller would be wrong more often; both are a guess, and this one is
    written down. Nothing else in the tool assumes the choice.
    """
    best: dict[tuple[str, str], tuple[int, tuple[str, str, float, float]]] = {}
    for row in _tsv(archive):
        state, name, land = row[0], plain_name(row[3], row[4]), int(row[6])
        lat, lon = float(row[10]), float(row[11])
        rings = (bounds or {}).get(row[1])
        if rings and _miles_outside(rings, lon, lat) > OFFSHORE_MILES:
            lon, lat = point_on_land(rings)
            print(f"  moved   {name}, {state}: its Census point is off its land")
        key = (name.lower(), state)
        found = best.get(key)
        if found is None or land > found[0]:
            best[key] = (land, (name, state, round(lat, 4), round(lon, 4)))
    return sorted((v[1] for v in best.values()), key=lambda r: (r[1], r[0]))


def build_zips(archive: bytes) -> list[tuple[str, float, float]]:
    return sorted((row[0], round(float(row[5]), 4), round(float(row[6]), 4))
                  for row in _tsv(archive))


def _dbf_column(dbf: bytes, wanted: str) -> list[str]:
    count, header_len, record_len = struct.unpack_from("<IHH", dbf, 4)
    fields, at = [], 32
    while dbf[at] != 0x0D:
        name = dbf[at:at + 11].split(b"\0")[0].decode("latin-1")
        fields.append((name, dbf[at + 16]))
        at += 32
    values = []
    for index in range(count):
        at = header_len + index * record_len + 1
        value = ""
        for name, width in fields:
            if name == wanted:
                value = dbf[at:at + width].decode("latin-1").strip()
            at += width
        values.append(value)
    return values


def _shp_polygons(shp: bytes, kinds: tuple[int, ...] = (5,)
                  ) -> list[list[list[tuple[float, float]]]]:
    """Every shape as a list of parts of (lon, lat).

    Polygons (type 5) by default; polylines are type 3. The Z and M variants
    lay their x/y out the same way, so 13 and 15 read here too.
    """
    shapes, at = [], 100
    while at < len(shp):
        length = struct.unpack_from(">ii", shp, at)[1]
        body = at + 8
        kind = struct.unpack_from("<i", shp, body)[0]
        if kind == 0:                     # a null shape: a record with no geometry
            shapes.append([])
            at = body + length * 2
            continue
        if kind not in kinds:
            raise SystemExit(f"shape type {kind} is not one of {kinds}")
        parts_count, point_count = struct.unpack_from("<ii", shp, body + 36)
        starts = list(struct.unpack_from(f"<{parts_count}i", shp, body + 44))
        flat = struct.unpack_from(f"<{point_count * 2}d", shp,
                                 body + 44 + 4 * parts_count)
        rings = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < parts_count else point_count
            rings.append([(flat[2 * p], flat[2 * p + 1])
                          for p in range(start, end)])
        shapes.append(rings)
        at = body + length * 2
    return shapes


def simplify(points: list[tuple[float, float]], tolerance: float):
    """Ramer-Douglas-Peucker. Keeps the ends and every point further than
    `tolerance` from the line its neighbours would draw without it."""
    if len(points) < 3 or tolerance <= 0:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    limit = tolerance * tolerance
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        x1, y1 = points[first]
        x2, y2 = points[last]
        dx, dy = x2 - x1, y2 - y1
        span = dx * dx + dy * dy
        worst, at = -1.0, -1
        for index in range(first + 1, last):
            x, y = points[index]
            if span == 0:
                gap = (x - x1) ** 2 + (y - y1) ** 2
            else:
                along = ((x - x1) * dx + (y - y1) * dy) / span
                along = min(1.0, max(0.0, along))
                gap = (x - (x1 + along * dx)) ** 2 + (y - (y1 + along * dy)) ** 2
            if gap > worst:
                worst, at = gap, index
        if worst > limit:
            keep[at] = True
            stack += [(first, at), (at, last)]
    return [p for p, wanted in zip(points, keep) if wanted]


def build_outline(archive: bytes) -> dict[str, list[list[list[float]]]]:
    """{state code: [ring, ...]}, each ring a list of [lon, lat]."""
    codes = _dbf_column(_only_member(archive, ".dbf"), "STUSPS")
    shapes = _shp_polygons(_only_member(archive, ".shp"))
    outline: dict[str, list[list[list[float]]]] = {}
    for code, rings in zip(codes, shapes):
        kept = []
        for ring in rings:
            thinned = simplify(ring, OUTLINE_TOLERANCE)
            if len(thinned) < 4:      # not a shape any more; a coastal islet
                continue
            kept.append([[round(x, OUTLINE_DECIMALS), round(y, OUTLINE_DECIMALS)]
                         for x, y in thinned])
        if kept:
            outline[code] = kept
    return outline


def _inside(ring, x: float, y: float) -> bool:
    """Even-odd ray test of (x, y) against one closed ring of (lon, lat)."""
    hit = False
    for (xa, ya), (xb, yb) in zip(ring, ring[1:]):
        if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya) + xa:
            hit = not hit
    return hit


def _miles_outside(rings, x: float, y: float) -> float:
    """0 inside the shape; otherwise miles to its nearest edge (flat-earth
    approximation, good to a fraction of a percent over a few miles)."""
    if sum(_inside(r, x, y) for r in rings) % 2:
        return 0.0
    kx = math.cos(math.radians(y)) * MILES_PER_DEGREE
    best = math.inf
    for ring in rings:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            ax, ay = (x1 - x) * kx, (y1 - y) * MILES_PER_DEGREE
            vx, vy = (x2 - x1) * kx, (y2 - y1) * MILES_PER_DEGREE
            span = vx * vx + vy * vy
            t = 0.0 if span == 0 else max(0.0, min(1.0, -(ax * vx + ay * vy) / span))
            best = min(best, math.hypot(ax + t * vx, ay + t * vy))
    return best


def _area(ring) -> float:
    return 0.5 * abs(sum(x1 * y2 - x2 * y1
                         for (x1, y1), (x2, y2) in zip(ring, ring[1:])))


def point_on_land(rings) -> tuple[float, float]:
    """A point inside the largest ring: its centroid when that is inside
    (it usually is), else the middle of the widest crossing at the
    centroid's latitude -- a point on the surface by construction."""
    ring = max(rings, key=_area)
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if a == 0:                        # a sliver with no area: any vertex will do
        return ring[0]
    cx, cy = cx / (3 * a), cy / (3 * a)
    if _inside(ring, cx, cy):
        return cx, cy
    ys = [p[1] for p in ring]
    for y in (cy, (min(ys) + max(ys)) / 2):
        xs = sorted((xb - xa) * (y - ya) / (yb - ya) + xa
                    for (xa, ya), (xb, yb) in zip(ring, ring[1:])
                    if (ya > y) != (yb > y))
        if len(xs) >= 2:
            left, right = max(zip(xs[0::2], xs[1::2]), key=lambda p: p[1] - p[0])
            return (left + right) / 2, y
    return ring[0]


def place_bounds(archive: bytes) -> dict[str, list]:
    """{GEOID: rings} from the 1:500,000 place boundaries."""
    ids = _dbf_column(_only_member(archive, ".dbf"), "GEOID")
    return dict(zip(ids, _shp_polygons(_only_member(archive, ".shp"))))


def _ring_flat(points, q: int) -> list[int]:
    """[x0, y0, dx1, dy1, ...] in units of 1/q degree. Deltas are small
    numbers, and small numbers are most of what makes the file compress."""
    out, px, py = [], 0, 0
    for x, y in points:
        ix, iy = round(x * q), round(y * q)
        out += (ix - px, iy - py)
        px, py = ix, iy
    return out


def _features(parts, tolerance: float, q: int, closed: bool) -> list:
    """[[minx, miny, maxx, maxy, flat], ...], one per ring or line, with its
    box in the same 1/q units so the server can clip without decoding.

    A shape under 2 px across at the tier's largest scale (four tolerances)
    is left out: an islet or a hamlet's urban area drawn as a speck says
    nothing, and 5,575 of them were most of the coarse file."""
    out = []
    for part in parts:
        kept = simplify(part, tolerance)
        if len(kept) < (4 if closed else 2):
            continue                   # below half a pixel: not drawable
        xs, ys = [p[0] for p in kept], [p[1] for p in kept]
        if closed and max(max(xs) - min(xs), max(ys) - min(ys)) < 4 * tolerance:
            continue
        out.append([round(min(xs) * q), round(min(ys) * q),
                    round(max(xs) * q), round(max(ys) * q),
                    _ring_flat(kept, q)])
    return out


def basemap_sources(fetcher) -> dict[str, list]:
    """Every layer's raw parts, once, for all tiers to simplify from."""
    def parts(name, kinds=(5,)):
        archive = fetcher(BASEMAP_URLS[name])
        return archive, _shp_polygons(_only_member(archive, ".shp"), kinds)

    _, states = parts("state")
    land = [ring for shape in states for ring in shape]
    _, counties = parts("county")
    _, urban = parts("urban")
    roads_zip, roads = parts("roads", (3,))
    kinds = _dbf_column(_only_member(roads_zip, ".dbf"), "RTTYP")
    boxes = [(min(p[0] for p in r), min(p[1] for p in r),
              max(p[0] for p in r), max(p[1] for p in r), r) for r in land]
    lake_shapes = []
    for name in ("lakes", "lakes_na"):
        for shape in parts(name)[1]:
            if not shape:
                continue
            # Only lakes inside the land. The Great Lakes are already the
            # edge of the land (the Census states are clipped to their
            # shore), and Natural Earth's 1:10m shore drawn over a 1:500k
            # one would show two coastlines a mile apart.
            x, y = point_on_land(shape)
            if -170 < x < -64 and 17 < y < 72 and _land_at(boxes, x, y):
                lake_shapes.append(shape)
    county_zip = fetcher(BASEMAP_URLS["county"])
    county_ids = _dbf_column(_only_member(county_zip, ".dbf"), "GEOID")
    return {
        "land": land,
        "county": [ring for shape in counties for ring in shape],
        "urban": [ring for shape in urban for ring in shape],
        "road": [part for shape in roads for part in shape],
        "road_i": [part for shape, kind in zip(roads, kinds)
                   if kind in COARSE_ROADS for part in shape],
        "lake": [ring for shape in lake_shapes for ring in shape],
        "lake_fine": fine_lakes(lake_shapes, zip(county_ids, counties), fetcher),
        "cities": cities(fetcher(CITIES_URL)),
    }


def _box(rings, pad: float = 0.0):
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _meet(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def fine_lakes(lake_shapes, counties, fetcher) -> list:
    """The Census's own outline of every Natural Earth lake: its lakes and
    reservoirs whose interior lies in, or within LAKE_MATCH_MILES of, one.

    Only the counties a lake actually reaches are fetched: a county is
    wanted when one of the lake's vertices falls inside it or one of its
    vertices falls inside the lake (bounding boxes alone asked for 714
    counties, 336 MB)."""
    pad = LAKE_MATCH_MILES / MILES_PER_DEGREE
    lakes = [(_box(shape, pad), shape) for shape in lake_shapes]
    wanted = []
    for geoid, shape in counties:
        if not shape:
            continue
        box = _box(shape)
        near = [lake for lake in lakes if _meet(box, lake[0])]
        if any(_touches(shape, lake) for _, lake in near):
            wanted.append(geoid)
    # Two passes. First, pieces that lie in a Natural Earth lake (within its
    # error). Then every other piece nearby that the Census gives the SAME
    # NAME as one already matched: it splits a lake into many pieces, some
    # outside Natural Earth's older, high-water outline and some filed as a
    # stream or canal -- the Great Salt Lake came back in holes without it.
    reach = LAKE_SAME_NAME_MILES / MILES_PER_DEGREE
    near_boxes = [(_box(shape, reach), shape) for shape in lake_shapes]
    candidates = []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        archives = pool.map(lambda g: fetcher(AREAWATER_URL.format(g)), wanted)
        for archive in archives:
            dbf = _only_member(archive, ".dbf")
            kinds = _dbf_column(dbf, "MTFCC")
            names = _dbf_column(dbf, "FULLNAME")
            for kind, name, shape in zip(kinds, names,
                                         _shp_polygons(_only_member(archive, ".shp"))):
                if not shape or kind in NOT_LAKE_MTFCC:
                    continue
                box = _box(shape)
                if any(_meet(box, nbox) for nbox, _ in near_boxes):
                    candidates.append((kind, name, box, shape))
    kept, matched = [], set()
    for index, (kind, name, box, shape) in enumerate(candidates):
        if kind not in LAKE_MTFCC:
            continue
        near = [lake for lbox, lake in lakes if _meet(box, lbox)]
        if not near:
            continue
        x, y = point_on_land(shape)
        square_miles = (max(_area(r) for r in shape) * MILES_PER_DEGREE ** 2
                        * math.cos(math.radians(y)))
        if square_miles < LAKE_MIN_SQ_MI:
            continue
        if any(_miles_outside(lake, x, y) <= LAKE_MATCH_MILES for lake in near):
            kept += shape
            matched.add(index)
    # Grow each matched lake through its own pieces: a piece joins when it
    # has the same name as a kept piece AND lies within a mile of it. A name
    # alone is not enough -- "Mud Lk" is a hundred different lakes.
    gap = 1.0 / MILES_PER_DEGREE
    grown = True
    while grown:
        grown = False
        for index, (kind, name, box, shape) in enumerate(candidates):
            if index in matched or not name or kind not in LAKE_MTFCC | RIVER_MTFCC:
                continue
            wide = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
            if any(candidates[m][1] == name and _meet(wide, candidates[m][2])
                   for m in matched):
                kept += shape
                matched.add(index)
                grown = True
    print(f"  lakes   {len(wanted)} counties fetched, {len(kept)} rings kept")
    return kept


def _touches(a, b) -> bool:
    """Whether two shapes (lists of rings) share ground: a vertex of either
    inside the other. Every vertex, not a sample: a sample of 40 missed the
    county holding the west arm of the Great Salt Lake, and the lake was cut
    off at the county line."""
    for mine, other in ((a, b), (b, a)):
        box = _box(other)
        for ring in mine:
            for x, y in ring:
                if (box[0] <= x <= box[2] and box[1] <= y <= box[3]
                        and sum(_inside(r, x, y) for r in other) % 2):
                    return True
    return False


def cities(archive: bytes) -> list[tuple[str, str, int]]:
    """(name, state code, metro population) for the US's places of 50,000
    people or more, biggest first."""
    dbf = _only_member(archive, ".dbf")
    names = _dbf_column(dbf, "nameascii")   # the reader is Latin-1; these are ASCII
    states = _dbf_column(dbf, "adm1name")
    countries = _dbf_column(dbf, "adm0_a3")
    pops = _dbf_column(dbf, "pop_max")
    out = []
    for name, state, country, pop in zip(names, states, countries, pops):
        code = STATE_CODES.get(state)
        people = int(float(pop or 0))
        if country == "USA" and code and people >= min(CITY_MIN_POP.values()):
            out.append((CITY_ALIASES.get((name, code), name), code, people))
    return sorted(out, key=lambda c: (-c[2], c[0]))


def _land_at(boxes, x: float, y: float) -> bool:
    return any(_inside(ring, x, y) for x0, y0, x1, y1, ring in boxes
               if x0 <= x <= x1 and y0 <= y <= y1)


def build_basemap_tier(name: str, raw: dict[str, list]) -> dict:
    tier = TIERS[name]
    tolerance = 0.5 / tier["ppm"] / MILES_PER_DEGREE
    layers = {}
    for layer in tier["layers"]:
        source = {("road", "coarse"): "road_i",
                  ("lake", "fine"): "lake_fine"}.get((layer, name), layer)
        layers[layer] = _features(raw[source], tolerance, tier["q"],
                                  closed=layer != "road")
    return {"tier": name, "ppm": tier["ppm"], "q": tier["q"],
            "tolerance_deg": round(tolerance, 7), "layers": layers,
            "cities": [list(c) for c in raw.get("cities", ())
                       if c[2] >= CITY_MIN_POP[name]]}


def write_gz(path: Path, payload: bytes) -> str:
    """Write `payload` gzipped with no timestamp, and return its SHA-256.

    mtime=0 on purpose: gzip stamps the clock into the header, and a file
    whose hash changes every time it is built cannot be checked against the
    one that shipped.
    """
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(payload)
    blob = buffer.getvalue()
    path.write_bytes(blob)
    return hashlib.sha256(blob).hexdigest()


def as_csv(header: tuple[str, ...], rows) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data", type=Path,
                        help="where to write the three files (default: data)")
    parser.add_argument("--cache", type=Path, default=None,
                        help="keep the downloaded archives here and reuse them")
    parser.add_argument("--only", choices=("places", "zips", "outline", "basemap"),
                        action="append", help="build just this one; repeatable")
    args = parser.parse_args(argv)

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(args.only or ("places", "zips", "outline", "basemap"))
    report = []

    if "places" in wanted:
        print("us_places.csv.gz")
        rows = build_places(_only_member(fetch(PLACES_URL, args.cache), ".txt"),
                            place_bounds(fetch(PLACE_BOUNDS_URL, args.cache)))
        digest = write_gz(out / "us_places.csv.gz",
                          as_csv(("name", "state", "lat", "lon"), rows))
        report.append(("us_places.csv.gz", len(rows), digest, "rows"))

    if "zips" in wanted:
        print("us_zips.csv.gz")
        rows = build_zips(_only_member(fetch(ZIPS_URL, args.cache), ".txt"))
        digest = write_gz(out / "us_zips.csv.gz",
                          as_csv(("zip", "lat", "lon"), rows))
        report.append(("us_zips.csv.gz", len(rows), digest, "rows"))

    if "outline" in wanted:
        print("us_outline.json.gz")
        outline = build_outline(fetch(OUTLINE_URL, args.cache))
        payload = json.dumps(outline, separators=(",", ":"),
                             sort_keys=True).encode("utf-8")
        digest = write_gz(out / "us_outline.json.gz", payload)
        points = sum(len(ring) for rings in outline.values() for ring in rings)
        report.append(("us_outline.json.gz", points, digest, "points"))

    if "basemap" in wanted:
        print("basemap")
        raw = basemap_sources(lambda url: fetch(url, args.cache))
        for name in TIERS:
            tier = build_basemap_tier(name, raw)
            payload = json.dumps(tier, separators=(",", ":")).encode("utf-8")
            file = f"us_basemap_{name}.json.gz"
            digest = write_gz(out / file, payload)
            points = sum(len(f[4]) // 2 for feats in tier["layers"].values()
                         for f in feats)
            report.append((file, points, digest, "points"))
            for layer, feats in tier["layers"].items():
                print(f"  {name:7} {layer:7} {len(feats):6} features "
                      f"{sum(len(f[4]) // 2 for f in feats):8} points")

    print()
    for name, count, digest, unit in report:
        size = (out / name).stat().st_size
        print(f"{name:26} {count:>8} {unit:6} {size / 1024:7.1f} KB  {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
