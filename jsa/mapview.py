"""The picture of the radius: geometry only, no markup and no network.

n18 put "within [N] miles of [here]" on the Matches page and answered it in
numbers. Numbers answer "how many"; they do not answer "does fifty miles
reach Example Town", which is the question somebody asks before they widen it.

The property that makes this worth drawing at all is that the picture cannot
disagree with the filter. It is not projected and then measured -- it is
measured and then drawn. Every posting's distance comes from the same
`places.nearest` the filter uses, and distance from the centre is the only
thing that decides where a dot sits, so a dot is inside the drawn circle if
and only if the posting is inside the radius. By construction, not by luck.

Two views, because one projection cannot serve both scales:

- LOCAL, when a radius is set. Azimuthal equidistant about the operator:
  bearing sets the direction, distance sets the length, and the circle is a
  real circle. No coastline -- the shipped outline is a 1:20,000,000
  generalisation, and at fifty miles across it would draw a straight line
  across the mouth of a bay and put a real job in the sea.
- NATIONAL, when the answer is "anywhere". Albers equal-area conic, the
  projection the lower 48 is the right shape in, with the outline drawn at
  the scale it is true at.

Nothing here reaches the network. A tile layer would send the operator's home
location to a map server on every page load, one request per tile; ADR 0014
refused a geocoder for that reason and the reason has not changed.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import places
from .config import ROOT

OUTLINE_FILE = ROOT / "data" / "us_outline.json.gz"

# Not drawn on the national map, and counted in words instead. Alaska and
# Hawaii at their true position turn the lower 48 into a thumbnail, and an
# inset is a second scale on one picture. Measured 2026-09-27: 0 of 998
# unreviewed postings place in either, so an inset would be decoration.
OFFSHORE = ("AK", "HI", "PR", "VI", "GU", "MP", "AS")

# Albers equal-area conic for the United States: the parameters everybody
# uses, so the shape is the one people recognise.
ALBERS_PARALLELS = (29.5, 45.5)
ALBERS_ORIGIN = (37.5, -96.0)

LOCAL_SIZE = (560, 440)
NATIONAL_SIZE = (900, 540)
PAD = 14

# A bubble is one place, not one posting: sixty jobs in Los Angeles are one
# town, and sixty dots on one pixel is a lie about how many places there are.
# Area grows with the count, which is how a reader reads circle size.
BUBBLE_MIN = 3.4
BUBBLE_MAX = 15.0

# How much further than the radius the local map looks. Enough to show what
# is just outside -- a job 27 miles away when the slider says 25 is a reason
# to move the slider, and a map cropped at the circle cannot say so.
LOCAL_REACH = 1.55


@dataclass(frozen=True)
class Bubble:
    """One place, and every posting in it."""

    x: float
    y: float
    r: float
    miles: float | None
    inside: bool
    place: str
    count: int
    job_id: int | None
    title: str


@dataclass(frozen=True)
class Label:
    x: float
    y: float
    text: str


@dataclass
class MapView:
    kind: str                       # "local" | "national" | "none"
    width: int = 0
    height: int = 0
    bubbles: list[Bubble] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    home: tuple[float, float] | None = None
    home_name: str = ""
    circle: float = 0.0             # px, 0 when there is no radius
    radius: float = 0.0             # miles, for the circle's own label
    per_mile: float = 0.0           # px per mile, so the page can rescale it
    scale_miles: int = 0
    scale_px: float = 0.0
    remote: int = 0                 # counted in words, never drawn
    unplaced: int = 0
    off_map: int = 0

    @property
    def drawn(self) -> bool:
        return self.kind != "none"

    @property
    def shown(self) -> int:
        return sum(b.count for b in self.bubbles)


def bearing(a: places.Place, b: places.Place) -> float:
    """Initial great-circle bearing a->b, radians clockwise from north.

    In an azimuthal equidistant projection centred on `a` this is the angle
    the point sits at, exactly -- which is why the drawing can be trusted.
    """
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlon = math.radians(b.lon - a.lon)
    y = math.sin(dlon) * math.cos(lat2)
    x = (math.cos(lat1) * math.sin(lat2)
         - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return math.atan2(y, x)


def albers(lat: float, lon: float) -> tuple[float, float]:
    """Albers equal-area conic, in unit-radius map coordinates (x east, y north)."""
    lat1, lat2 = map(math.radians, ALBERS_PARALLELS)
    lat0, lon0 = math.radians(ALBERS_ORIGIN[0]), math.radians(ALBERS_ORIGIN[1])
    n = 0.5 * (math.sin(lat1) + math.sin(lat2))
    c = math.cos(lat1) ** 2 + 2 * n * math.sin(lat1)
    rho0 = math.sqrt(c - 2 * n * math.sin(lat0)) / n
    phi, lam = math.radians(lat), math.radians(lon)
    rho = math.sqrt(max(0.0, c - 2 * n * math.sin(phi))) / n
    theta = n * (lam - lon0)
    return rho * math.sin(theta), rho0 - rho * math.cos(theta)


@lru_cache(maxsize=1)
def outline() -> dict[str, list[list[list[float]]]]:
    """State rings as [[lon, lat], ...], or {} when the file is missing."""
    if not OUTLINE_FILE.exists():
        return {}
    with gzip.open(OUTLINE_FILE, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _bubble_radius(count: int, most: int) -> float:
    if most <= 1:
        return BUBBLE_MIN
    share = math.sqrt(count) / math.sqrt(most)
    return BUBBLE_MIN + share * (BUBBLE_MAX - BUBBLE_MIN)


def _nice_miles(span: float) -> int:
    """A round number of miles, about a quarter of `span`, for the scale bar."""
    target = max(1.0, span / 4)
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if step >= target:
            return step
    return 1000


def _group(rows, origin: places.Place | None):
    """Postings by place. Returns (groups, remote, unplaced).

    A remote posting has no distance (n18) and is never a dot. One that
    cannot be placed is not a dot either: unknown is not far away, and a
    guessed position is the one mistake this module must not make.
    """
    groups: dict[str, dict] = {}
    remote = unplaced = 0
    for row in rows:
        if (row.get("remote") or "") == "remote":
            remote += 1
            continue
        placement = places.parse(row.get("location") or "")
        if not placement.places:
            unplaced += 1
            continue
        spot = placement.places[0]
        if origin is not None:
            spot = min(placement.places, key=lambda p: places.miles(origin, p))
        key = str(spot)
        group = groups.setdefault(key, {"place": spot, "count": 0,
                                        "job_id": None, "title": ""})
        group["count"] += 1
        if group["job_id"] is None:
            group["job_id"] = row.get("job_id")
            group["title"] = row.get("title") or ""
    return groups, remote, unplaced


def local(rows, origin: places.Place, radius: float,
          size: tuple[int, int] = LOCAL_SIZE) -> MapView:
    """The map of one radius, centred on the operator.

    `rows` is the list BEFORE the radius filter, so the map can show what is
    just outside it. A bubble is inside the drawn circle exactly when its
    distance is within `radius`.
    """
    width, height = size
    view = MapView("local", width, height, radius=radius,
                   home=(width / 2, height / 2), home_name=str(origin))
    groups, view.remote, view.unplaced = _group(rows, origin)

    reach = max(radius * LOCAL_REACH, 1.0)
    usable = min(width, height) / 2 - PAD
    per_mile = usable / reach
    view.circle = radius * per_mile
    view.per_mile = per_mile
    view.scale_miles = _nice_miles(reach)
    view.scale_px = view.scale_miles * per_mile

    most = max((g["count"] for g in groups.values()), default=1)
    placed: list[Bubble] = []
    for group in sorted(groups.values(), key=lambda g: -g["count"]):
        distance = places.miles(origin, group["place"])
        if distance > reach:
            view.off_map += group["count"]
            continue
        angle = bearing(origin, group["place"])
        placed.append(Bubble(
            x=width / 2 + per_mile * distance * math.sin(angle),
            y=height / 2 - per_mile * distance * math.cos(angle),
            r=_bubble_radius(group["count"], most),
            miles=round(distance),
            # The one line that has to be right: same comparison, same
            # numbers, as jsa.web._by_distance.
            inside=distance <= radius,
            place=group["place"].name, count=group["count"],
            job_id=group["job_id"], title=group["title"]))
    view.bubbles = placed
    # The centre already carries the operator's own town as a label, so a
    # bubble sitting on top of it does not need a second one.
    view.labels = _labels([b for b in placed if b.miles], most=6, apart=46)
    return view


def national(rows, origin: places.Place | None = None,
             size: tuple[int, int] = NATIONAL_SIZE) -> MapView:
    """Every placeable posting on the lower 48. No circle: no radius to draw."""
    width, height = size
    view = MapView("national", width, height)
    shapes = outline()
    lower48 = {code: rings for code, rings in shapes.items()
               if code not in OFFSHORE}
    if not lower48:
        # No outline file. A scatter of dots with nothing behind it is not a
        # map of anything, so say there is no map rather than draw a bad one.
        return MapView("none")

    xs, ys = [], []
    for rings in lower48.values():
        for ring in rings:
            for lon, lat in ring:
                x, y = albers(lat, lon)
                xs.append(x)
                ys.append(y)
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    scale = min((width - 2 * PAD) / span_x, (height - 2 * PAD) / span_y)
    left = (width - span_x * scale) / 2
    top = (height - span_y * scale) / 2
    min_x, max_y = min(xs), max(ys)

    def project(lat: float, lon: float) -> tuple[float, float]:
        x, y = albers(lat, lon)
        return left + (x - min_x) * scale, top + (max_y - y) * scale

    for code, rings in sorted(lower48.items()):
        for ring in rings:
            points = [project(lat, lon) for lon, lat in ring]
            view.paths.append(
                "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points) + "Z")

    groups, view.remote, view.unplaced = _group(rows, None)
    most = max((g["count"] for g in groups.values()), default=1)
    placed: list[Bubble] = []
    for group in sorted(groups.values(), key=lambda g: -g["count"]):
        spot = group["place"]
        if spot.state in OFFSHORE:
            view.off_map += group["count"]
            continue
        x, y = project(spot.lat, spot.lon)
        placed.append(Bubble(x=x, y=y, r=_bubble_radius(group["count"], most),
                             miles=None, inside=True, place=spot.name,
                             count=group["count"], job_id=group["job_id"],
                             title=group["title"]))
    view.bubbles = placed
    view.labels = _labels(placed)
    if origin is not None and origin.state not in OFFSHORE:
        view.home = project(origin.lat, origin.lon)
        view.home_name = str(origin)

    # Measure the scale bar off the projection rather than asserting a width
    # for the country: two points on the origin parallel, their true distance,
    # and the pixels between them where they land. A conic projection's scale
    # varies with latitude, so this is the scale in the middle of the map and
    # the bar is honest to a few percent, not to the mile.
    lat = ALBERS_ORIGIN[0]
    west = places.Place("", "", lat, ALBERS_ORIGIN[1] - 5)
    east = places.Place("", "", lat, ALBERS_ORIGIN[1] + 5)
    wx, wy = project(west.lat, west.lon)
    ex, ey = project(east.lat, east.lon)
    per_mile = math.hypot(ex - wx, ey - wy) / places.miles(west, east)
    view.scale_miles = _nice_miles((width - 2 * PAD) / per_mile / 2)
    view.scale_px = view.scale_miles * per_mile
    return view


def points(rows, origin: places.Place) -> tuple[list[dict], int, int]:
    """Every placeable posting as a point in miles about `origin`, for the
    dashboard's interactive map. Returns (points, remote, unplaced).

    The same azimuthal-equidistant geometry as `local`, without the pixels:
    x east, y north, and `d` the posting's distance -- the exact float
    `places.nearest` gives, so the browser compares the very number the
    server's radius filter compared. Rounding x and y is for size only; the
    browser decides inside/outside from `d`, never from x and y.

    No titles: a posting the radius hides must not be on the page at all,
    and this list holds every posting, inside the circle or not.
    """
    out: list[dict] = []
    remote = unplaced = 0
    for row in rows:
        if (row.get("remote") or "") == "remote":
            remote += 1
            continue
        placement = places.parse(row.get("location") or "")
        if not placement.places:
            unplaced += 1
            continue
        spot = min(placement.places, key=lambda p: places.miles(origin, p))
        distance = places.miles(origin, spot)
        angle = bearing(origin, spot)
        out.append({
            "key": row.get("key") or f"job:{row.get('job_id')}",
            "job": row.get("job_id"),
            "status": row.get("status") or "new",
            "place": spot.name,
            "x": round(distance * math.sin(angle), 3),
            "y": round(distance * math.cos(angle), 3),
            "d": distance,
        })
    return out, remote, unplaced


def _labels(bubbles: list[Bubble], most: int = 5, apart: float = 58.0):
    """Name the places with the most postings, and only those.

    Not "the towns nearby": the shipped Gazetteer has no population, so
    "nearby and notable" could only be guessed, and a map that names a hamlet
    while leaving a city blank is worse than one that names neither. What
    this map is about is where the jobs are, so it names where the jobs are.
    """
    out: list[Label] = []
    for bubble in bubbles[:12]:
        if len(out) >= most:
            break
        if any(math.hypot(bubble.x - label.x, bubble.y - label.y) < apart
               for label in out):
            continue
        out.append(Label(bubble.x, bubble.y - bubble.r - 5, bubble.place))
    return out


def build(rows, origin: places.Place | None, radius: float | None,
          local_size: tuple[int, int] = LOCAL_SIZE,
          national_size: tuple[int, int] = NATIONAL_SIZE) -> MapView:
    """Whichever map answers the question the page is asking."""
    if origin is not None and radius:
        return local(rows, origin, float(radius), local_size)
    return national(rows, origin, national_size)
