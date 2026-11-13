"""Build Canada's shipped data files in jsa/resources/data/ca/ from Statistics Canada.

Run it and diff:

    python tools/build_ca_data.py --out jsa/resources/data --cache <somewhere>

It asks Statistics Canada's public map service for the 2021 census
boundaries, keeps one point on land per area, and prints the count and
SHA-256 of each file it writes (gzip with mtime=0, so the same answers give
the same bytes). --cache keeps the answers (about 30 MB of JSON) so a second
run asks for nothing.

This is a BUILD tool, as tools/build_map_data.py is: nothing in jsa/
imports it.

Source and licence. Statistics Canada, 2021 Census cartographic boundary
files (catalogue 92-160-X, 92-179-X), served at
  https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer
under the Statistics Canada Open Licence
(https://www.statcan.gc.ca/en/terms-conditions/open-licence). The files built
here are "Adapted from Statistics Canada, 2021 Census boundary files,
2021. This does not constitute an endorsement by Statistics Canada of this
product." The forward sortation area file includes data copied with
permission from Canada Post Corporation; "postal code" is an official mark
of Canada Post. Only the three-character areas are used: the app never
stores or looks up a full postal code (ADR 0037).

  layer 9,  census subdivisions,
  layer 7,  population centres  -> data/ca/ca_places.csv.gz  (a point per town)
  layer 14, forward sortation   -> data/ca/ca_fsa.csv.gz     (a point per area)
  layer 0,  provinces           -> data/ca/ca_outline.json.gz (the national map)

The ground under the live map (--only basemap): the US basemap's three tiers
and layers, built the same way (tools/build_map_data.py TIERS, ADR 0019).
  Cartographic boundary files
    layer 0, provinces (shore-clipped; the big lakes are holes)
                                -> land, and lake (those holes)
    layer 4, census divisions   -> county (fine tier only, as in the US)
    layer 7, population centres -> urban
  Road Network File 2021 (same licence), served at
    https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Road_Network_File/MapServer
    rank 1 (coarse), ranks 1-2  -> road
  Natural Earth 10m lakes (public domain)
                                -> lake, coarse and medium tiers
  Natural Resources Canada, CanVec 1:250 000 Hydro Features, waterbodies
  (Open Government Licence - Canada,
  https://open.canada.ca/en/open-government-licence-canada), served at
    https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/canvec/shp/Hydro/
                                -> lake, fine tier
  Natural Earth 10m populated places (public domain)
                                -> city names
                                -> data/ca/ca_basemap_{coarse,medium,fine}.json.gz
"Contains information licensed under the Open Government Licence - Canada."
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import struct
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_map_data import (BASEMAP_URLS, CITIES_URL, CITY_MIN_POP,  # noqa: E402
                            LAKE_MATCH_MILES, MILES_PER_DEGREE, TIERS, _area,
                            _dbf_column, _features, _inside, _miles_outside,
                            _only_member, _shp_polygons, fetch, point_on_land,
                            simplify, write_gz)

REST = "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/"
SERVICE = REST + "Cartographic_boundary_files/MapServer"
ROADS = REST + "Road_Network_File/MapServer"
UA = "jobsearch-assistant data build (tools/build_ca_data.py)"

# Statistics Canada's province codes and the two-letter ones a posting uses.
PROVINCES = {"10": "NL", "11": "PE", "12": "NS", "13": "NB", "24": "QC", "35": "ON",
             "46": "MB", "47": "SK", "48": "AB", "59": "BC", "60": "YT", "61": "NT",
             "62": "NU"}

# Census subdivision types left out: reserves, settlements and unorganized
# areas. A posting names a town, not these, and their names would only add
# look-alikes ("Bear River (Part) 6").
SKIP_TYPES = {"IRI", "S-É", "TC", "TI", "NO", "SNO", "IGD", "NL", "NVL", "VN", "SET"}
# Rural types that often share a town's name (the parish of Grand Falls
# around the town of Grand Falls): the town wins.
RURAL_TYPES = {"P", "PE", "CT", "RM", "MD", "DM", "CU", "COM"}

PAGE = 1000
# Server-side simplification, in degrees. Points only need a ring that is
# the right shape at town scale; the outline needs about a kilometre.
POINT_OFFSET, OUTLINE_OFFSET = 0.002, 0.02
# Outline rings smaller than this (square degrees) are dropped: the Arctic
# has thousands of islets that cost bytes and show as nothing.
MIN_RING = 0.05


def query(layer: int, fields: str, offset: float, cache: Path | None,
          service: str = SERVICE, where: str = "1=1", page: int = PAGE) -> list[dict]:
    """Every feature of a layer, in pages, as {attributes, rings}; a line
    layer's paths come back as its rings. A page the service refuses (an
    error 500, which is how it says a page is too big) is asked for again
    in halves."""
    out: list[dict] = []
    start = 0
    tag = ("" if service == SERVICE else "roads_") + (
        "" if where == "1=1" else "".join(c for c in where if c.isalnum()) + "_")
    while True:
        params = {"where": where, "outFields": fields, "returnGeometry": "true",
                  "outSR": "4326", "maxAllowableOffset": str(offset),
                  "geometryPrecision": "4", "orderByFields": "OBJECTID",
                  "resultOffset": str(start), "resultRecordCount": str(page), "f": "json"}
        url = f"{service}/{layer}/query?{urllib.parse.urlencode(params)}"
        name = f"statcan_{tag}{layer}_{offset}_{start}_{page}.json"
        cached = cache / name if cache else None
        if cached and cached.exists():
            body = cached.read_bytes()
            print(f"  cached  {name}")
        else:
            print(f"  fetch   layer {layer}, from {start}")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = resp.read()
        data = json.loads(body)
        if "error" in data:
            if page > 1:
                page = max(1, page // 2)
                continue
            raise SystemExit(f"Statistics Canada refused the query: {data['error']}")
        if cached and not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(body)
        feats = data.get("features") or []
        for f in feats:
            geometry = f.get("geometry") or {}
            rings = [[(x, y) for x, y in ring]
                     for ring in geometry.get("rings") or geometry.get("paths") or []]
            if rings:
                out.append({"a": f["attributes"], "rings": rings})
        if len(feats) < page and not data.get("exceededTransferLimit"):
            return out
        start += page


def _names(text: str) -> list[str]:
    """"Grand Falls / Grand-Sault" -> both; "Ottawa - Gatineau" -> both."""
    return [n.strip() for part in text.split(" / ") for n in part.split(" - ") if n.strip()]


def build_places(feats: list[dict], centres: list[dict]) -> list[tuple[str, str, float, float]]:
    """(name, province, lat, lon): one row per town name, and a row for each
    half of a bilingual name ("Grand Falls / Grand-Sault").

    The point comes from the smaller of two areas with the town's name: the
    census subdivision, or the population centre. A subdivision can be a
    whole amalgamated region (Ottawa, Halifax) whose middle is countryside;
    a population centre can be a whole metro (Toronto's runs to Oshawa).
    The smaller one is the town. Where one name appears twice in a
    province, a town beats a rural type, then the larger area wins.

    Checked against 15 city halls (2026-10-30): 7 within 2 km, 13 within
    9 km, Halifax 12 km (its population centre spans the harbour)."""
    core: dict[tuple[str, str], tuple[float, float, float]] = {}
    for f in centres:
        prov = PROVINCES[f["a"]["PRUID"]]
        x, y = point_on_land(f["rings"])
        area = float(f["a"].get("LANDAREA") or 0)
        for name in _names(f["a"]["PCNAME"]):
            if area > core.get((name, prov), (-1,))[0]:
                core[(name, prov)] = (area, y, x)
    best: dict[tuple[str, str], tuple[tuple[int, float], float, float]] = {}
    for f in feats:
        a = f["a"]
        if a["CSDTYPE"] in SKIP_TYPES:
            continue
        prov = PROVINCES[a["PRUID"]]
        area = float(a.get("LANDAREA") or 0)
        rank = (0 if a["CSDTYPE"] in RURAL_TYPES else 1, area)
        x, y = point_on_land(f["rings"])
        for name in _names(a["CSDNAME"]):
            key = (name, prov)
            if key not in best or rank > best[key][0]:
                if key in core and core[key][0] < area:
                    _, y2, x2 = core[key]
                    best[key] = (rank, y2, x2)
                else:
                    best[key] = (rank, y, x)
    return sorted((n, p, round(lat, 4), round(lon, 4))
                  for (n, p), (_, lat, lon) in best.items())


def build_fsa(feats: list[dict]) -> list[tuple[str, float, float]]:
    """(area, lat, lon). An urban area (second character not 0) drawn in
    pieces is placed on the piece nearest its neighbours (the areas sharing
    its first two characters): the 2021 file draws T2P, downtown Calgary,
    with a second, larger piece 30 km east, and the largest piece would put
    downtown on the prairie."""
    first = {f["a"]["CFSAUID"]: point_on_land(f["rings"]) for f in feats}
    rows = []
    for f in feats:
        code = f["a"]["CFSAUID"]
        x, y = first[code]
        if code[1] != "0" and len(f["rings"]) > 1:
            near = [p for c, p in first.items() if c[:2] == code[:2] and c != code]
            if near:
                mx = sorted(p[0] for p in near)[len(near) // 2]
                my = sorted(p[1] for p in near)[len(near) // 2]
                x, y = min((point_on_land([r]) for r in f["rings"]),
                           key=lambda p: (p[0] - mx) ** 2 + (p[1] - my) ** 2)
        rows.append((code, round(y, 4), round(x, 4)))
    return sorted(rows)


def build_outline(feats: list[dict]) -> dict[str, list[list[list[float]]]]:
    out: dict[str, list] = {}
    for f in feats:
        prov = PROVINCES[f["a"]["PRUID"]]
        for ring in f["rings"]:
            if _area(ring) < MIN_RING:
                continue
            pts = simplify(ring, OUTLINE_OFFSET)
            if len(pts) >= 4:
                out.setdefault(prov, []).append([[round(x, 3), round(y, 3)] for x, y in pts])
    return dict(sorted(out.items()))


# The basemap's ground is fetched once, simplified on the server to half a
# pixel at the fine tier's scale, then each tier simplifies further from it,
# exactly as tools/build_map_data.py does for the US.
GROUND_OFFSET = round(0.5 / TIERS["fine"]["ppm"] / MILES_PER_DEGREE, 6)
# Road Network File ranks: 1 is the Trans-Canada and the other freeways, the
# interstates' counterpart (the coarse tier); 2 the other major highways,
# the rest of what the US file's primary roads are.
COARSE_RANKS, ROAD_RANKS = {"1"}, ("1", "2")


def _signed(ring) -> float:
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))


# An island smaller than this share of its division's largest piece is left
# out of the county layer (its shore is the land's own edge).
COUNTY_ISLAND = 0.01


def _boxed(rings):
    return [(min(p[0] for p in r), min(p[1] for p in r),
             max(p[0] for p in r), max(p[1] for p in r), r) for r in rings]


def _in_any(boxes, x: float, y: float) -> bool:
    return any(x0 <= x <= x1 and y0 <= y <= y1 and _inside(r, x, y)
               for x0, y0, x1, y1, r in boxes)


def natural_earth_lakes(land, holes, cache) -> list:
    """The lakes the boundary file leaves in the land: Natural Earth's
    (public domain, the US map's own lakes at its two wider tiers), each
    kept when a point inside it is on Canada's land and not in a lake the
    boundary file already cut out, so no lake is drawn twice. A list of
    shapes (each a list of rings)."""
    land_boxes, hole_boxes = _boxed(land), _boxed(holes)
    out = []
    for name in ("lakes", "lakes_na"):
        archive = fetch(BASEMAP_URLS[name], cache)
        for shape in _shp_polygons(_only_member(archive, ".shp")):
            if not shape:
                continue
            x, y = point_on_land(shape)
            if _in_any(land_boxes, x, y) and not _in_any(hole_boxes, x, y):
                out.append(shape)
    print(f"  lakes   {len(holes)} from the boundary file, "
          f"{sum(map(len, out))} rings from Natural Earth")
    return out


# The fine tier's lakes. Natural Earth's are 1:10,000,000, a kilometre off
# up close (Lake Simcoe's shore: median 0.7 km, worst 3 km from the national
# 1:50,000 water, NHN). As the US fine tier draws the SAME lakes from the
# Census's own water, Canada's redraws them from NRCan's CanVec 1:250,000
# waterbodies (Simcoe: median 15 m from the NHN shore, 95% within 330 m;
# stated accuracy 150 to 250 m, NTDB). The 1:50,000 series is 6.6 GB of
# waterbodies for the country, and Statistics Canada's own "lakes and
# rivers" file sits behind a browser check, so neither suits a build.
CANVEC_URL = ("https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/canvec/shp/Hydro/"
              "canvec_250K_{}_Hydro_shp.zip")
KM_PER_DEGREE = 111.32
# Only the lakes near where people live are redrawn: within LAKE_NEAR_KM of
# a population centre, where the fine tier is looked at. Every lake redrawn
# costs its whole shore (Lake Winnipeg alone is 20,000 points at this tier),
# and all of them came to 4 MB more; these come to 0.6 MB (2026-11-12).
# The rest keep Natural Earth's outline, as at the medium tier.
LAKE_NEAR_KM = 10.0
# CanVec cuts a lake at its 1:250,000 sheet lines: every piece of a redrawn
# lake down to PIECE_MIN_KM2 is drawn, or the lake has gaps. An island under
# ISLAND_MIN_KM2 is drawn as water: the thousands of islands of Lake of the
# Woods and its like were half the bytes.
PIECE_MIN_KM2, ISLAND_MIN_KM2 = 1.0, 1.0


def _km2(ring) -> float:
    ys = [p[1] for p in ring]
    return _area(ring) * KM_PER_DEGREE ** 2 * math.cos(math.radians((min(ys) + max(ys)) / 2))


def _big_polygons(shp: bytes, min_km2: float):
    """Each polygon in a shapefile whose bounding box could hold `min_km2`,
    as a list of rings. The others are skipped from their record header,
    unread: a province's waterbodies are up to 320 MB, nearly all ponds."""
    at = 100
    while at < len(shp):
        length = struct.unpack_from(">i", shp, at + 4)[0]
        body, at = at + 8, at + 8 + length * 2
        if struct.unpack_from("<i", shp, body)[0] not in (5, 15, 25):
            continue
        x0, y0, x1, y1 = struct.unpack_from("<4d", shp, body + 4)
        if (x1 - x0) * (y1 - y0) * KM_PER_DEGREE ** 2 * math.cos(math.radians(y1)) < min_km2:
            continue
        parts, count = struct.unpack_from("<ii", shp, body + 36)
        starts = list(struct.unpack_from(f"<{parts}i", shp, body + 44)) + [count]
        flat = struct.unpack_from(f"<{count * 2}d", shp, body + 44 + 4 * parts)
        yield [[(flat[2 * p], flat[2 * p + 1]) for p in range(starts[i], starts[i + 1])]
               for i in range(parts)]


def _padded_box(rings, pad: float):
    boxes = _boxed(rings)
    return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)


def _meets(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def fine_lakes(holes, ne_shapes, urban, cache) -> list:
    """The fine tier's lake rings: the boundary file's holes; every Natural
    Earth lake within LAKE_NEAR_KM of a population centre, redrawn from the
    CanVec waterbodies that lie in it (within LAKE_MATCH_MILES, its error),
    so a lake sharpens as you zoom in rather than appearing; and the other
    Natural Earth lakes as they are. Rings keep their winding, so an island
    stays land under the page's nonzero fill."""
    near_deg = LAKE_NEAR_KM / KM_PER_DEGREE
    urban_boxes = _boxed(urban)
    near = set()
    for index, shape in enumerate(ne_shapes):
        reach = _padded_box(shape, near_deg / math.cos(math.radians(shape[0][0][1])))
        for x0, y0, x1, y1, ring in urban_boxes:
            if _meets(reach, (x0, y0, x1, y1)) and any(
                    _miles_outside(shape, x, y) * 1.609 <= LAKE_NEAR_KM
                    for x, y in ring[::max(1, len(ring) // 20)]):
                near.add(index)
                break
    pad = LAKE_MATCH_MILES / MILES_PER_DEGREE
    ne_boxes = [_padded_box(shape, pad) for shape in ne_shapes]
    pieces, seen = [], set()        # (rings kept, the Natural Earth lakes it lies in)
    for code in PROVINCES.values():
        shp = _only_member(fetch(CANVEC_URL.format(code), cache), "waterbody_2.shp")
        for rings in _big_polygons(shp, PIECE_MIN_KM2):
            outer = max(rings, key=_area)
            if _km2(outer) < PIECE_MIN_KM2:
                continue
            key = (round(outer[0][0], 6), round(outer[0][1], 6), len(outer))
            if key in seen:             # a lake on a provincial line, in both files
                continue
            seen.add(key)
            box = _padded_box([outer], 0)
            hits = [i for i, nbox in enumerate(ne_boxes) if _meets(box, nbox)]
            if not hits:
                continue
            sample = [point_on_land([outer])] + outer[::max(1, len(outer) // 10)]
            lakes = {i for i in hits
                     if any(_miles_outside(ne_shapes[i], x, y) <= LAKE_MATCH_MILES
                            for x, y in sample)}
            if lakes:
                pieces.append(([outer] + [r for r in rings if r is not outer
                                          and _km2(r) >= ISLAND_MIN_KM2], lakes))
    # A piece in a redrawn lake and in another (two lakes joined in CanVec)
    # redraws that one too, or both would be drawn.
    redrawn = set(near)
    while True:
        grown = set().union(*(lakes for _, lakes in pieces if lakes & redrawn))
        if grown <= redrawn:
            break
        redrawn |= grown
    drawn = [rings for rings, lakes in pieces if lakes & redrawn]
    redrawn &= set().union(*(lakes for _, lakes in pieces if lakes & redrawn))
    # A boundary-file hole that a redrawn lake already covers is left out:
    # the boundary file is generalized (a hole north of Kenora was 870 m off
    # CanVec's shore), and drawn under the CanVec water it showed land as
    # water. Covered: a point inside it is in a CanVec piece, and most of its
    # shore is within twice the match distance of that water.
    outers = _boxed([rings[0] for rings in drawn])
    reach = 2 * LAKE_MATCH_MILES

    def covered(hole) -> bool:
        if not _in_any(outers, *point_on_land([hole])):
            return False
        box = _padded_box([hole], reach / MILES_PER_DEGREE * 2)
        near = [rings for rings in drawn if _meets(box, _padded_box(rings[:1], 0))]
        sample = hole[::max(1, len(hole) // 20)]
        close = sum(any(_miles_outside(rings[:1], x, y) <= reach for rings in near)
                    for x, y in sample)
        return close >= 0.8 * len(sample)

    kept = [hole for hole in holes if not covered(hole)]
    print(f"  lakes   fine: {len(holes) - len(kept)} boundary-file holes left out, "
          "covered by redrawn lakes")
    out = kept + [r for rings in drawn for r in rings]
    out += [r for i, shape in enumerate(ne_shapes) if i not in redrawn for r in shape]
    print(f"  lakes   fine: {len(redrawn)} of {len(ne_shapes)} Natural Earth lakes redrawn "
          f"from {len(drawn)} CanVec pieces, {len(out)} rings")
    return out


def ground(cache: Path | None) -> dict[str, list]:
    """Every layer's raw parts, once, for all tiers to simplify from.

    The provinces are shore-clipped, with the large lakes cut out as holes
    (anticlockwise rings in Esri's geometry). The outer rings are the land;
    the holes are the lake layer, drawn over it, so a lake has its own fill
    and edge as a US one does, and its shore is the boundary file's own."""
    land, holes = [], []
    # One province at a time: the whole country at this detail is more than
    # the service will answer in one go (it returns an error 500).
    for code in PROVINCES:
        for f in query(0, "PRUID", GROUND_OFFSET, cache, where=f"PRUID='{code}'"):
            for ring in f["rings"]:
                (holes if _signed(ring) > 0 else land).append(ring)
    county = []
    for f in query(4, "CDUID", GROUND_OFFSET, cache, page=25):
        # A division's islands are only its coastline again, which the land
        # already draws: the dashed line is for the division's own edge.
        big = max(_area(r) for r in f["rings"])
        county += [r for r in f["rings"] if _area(r) >= COUNTY_ISLAND * big]
    urban = [r for f in query(7, "PCUID", GROUND_OFFSET, cache, page=100) for r in f["rings"]]
    road, road_i = [], []
    for rank in ROAD_RANKS:
        for f in query(int(rank) - 1, "RANK", GROUND_OFFSET, cache, service=ROADS):
            road += f["rings"]
            if str(f["a"].get("RANK")) in COARSE_RANKS:
                road_i += f["rings"]
    ne = natural_earth_lakes(land, holes, cache)
    lake = holes + [ring for shape in ne for ring in shape]
    return {"land": land, "lake": lake, "lake_fine": fine_lakes(holes, ne, urban, cache),
            "county": county, "urban": urban,
            "road": road, "road_i": road_i, "cities": cities(fetch(CITIES_URL, cache))}


# Natural Earth names that differ from Statistics Canada's own: the town a
# label must stand on. Chicoutimi has been part of Saguenay since 2002.
CITY_ALIASES = {("Sudbury", "ON"): "Greater Sudbury",
                ("Trois Rivieres", "QC"): "Trois-Rivières",
                ("St.-Jerome", "QC"): "Saint-Jérôme",
                ("Chicoutimi", "QC"): "Saguenay"}


def cities(archive: bytes) -> list[tuple[str, str, int]]:
    """(name, province code, metro population) for Canada's places of 50,000
    people or more, biggest first, from Natural Earth (as the US's are)."""
    from jsa.countries import ca
    dbf = _only_member(archive, ".dbf")
    names = _dbf_column(dbf, "nameascii")
    provinces = _dbf_column(dbf, "adm1name")
    countries = _dbf_column(dbf, "adm0_a3")
    pops = _dbf_column(dbf, "pop_max")
    out = []
    for name, prov, country, pop in zip(names, provinces, countries, pops):
        try:                        # the reader is Latin-1; the file is UTF-8
            prov = prov.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
        code = ca.REGION_BY_NAME.get(prov.strip().lower())
        people = int(float(pop or 0))
        if country == "CAN" and code and people >= min(CITY_MIN_POP.values()):
            out.append((CITY_ALIASES.get((name, code), name), code, people))
    return sorted(out, key=lambda c: (-c[2], c[0]))


def basemap_tier(name: str, raw: dict[str, list]) -> dict:
    """One tier, in the US files' exact shape (tools/build_map_data.py)."""
    tier = TIERS[name]
    tolerance = 0.5 / tier["ppm"] / MILES_PER_DEGREE
    layers = {}
    for layer in tier["layers"]:
        source = {("road", "coarse"): "road_i",
                  ("lake", "fine"): "lake_fine"}.get((layer, name), layer)
        layers[layer] = _features(raw[source], tolerance, tier["q"], closed=layer != "road")
    return {"tier": name, "ppm": tier["ppm"], "q": tier["q"],
            "tolerance_deg": round(tolerance, 7), "layers": layers,
            "cities": [list(c) for c in raw["cities"] if c[2] >= CITY_MIN_POP[name]]}


def _csv(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


# --- Who holds each kind of job (--only roles) ------------------------------------
# The Canadian twin of tools/build_role_data.py (plan 33), in the US files'
# exact columns so jsa.roles reads it unchanged (ADR 0037, 2026-11-11):
#   Census 2021, table 98-10-0449-01: highest certificate by occupation (NOC
#     2021 unit group), Canada, all genders, employed, ages 25-64 plus 65+
#     (workers 25 and older, as BLS) -> data/ca/role_education.csv.gz
#   NOC 2021 V1.0 classification structure and elements: class titles,
#     illustrative examples and the index of titles -> data/ca/role_titles.csv.gz
# Statistics Canada Open Licence; "Adapted from Statistics Canada, ...".
CENSUS_TABLE = ("https://www150.statcan.gc.ca/t1/tbl1/en/"
                "dtl!downloadDbLoadingData-nonTraduit.action")
NOC_URLS = {
    "structure": "https://www.statcan.gc.ca/en/subjects/standard/noc/2021/indexV1/"
                 "noc-2021-v1.0-classification-structure.csv",
    "elements": "https://www.statcan.gc.ca/en/subjects/standard/noc/2021/indexV1/"
                "noc-2021-v1.0-elements.csv",
}
# Member ids of the table's dimensions (WDS getCubeMetadata, productId
# 98100449): geography Canada; all 16 certificate rows; ages 25-64 and 65+;
# all genders; occupations 1-821; employed. More than about 100 occupations in
# one request is refused (502).
CENSUS_OCCUPATIONS = 821
CENSUS_BATCH = 100
# Census certificate rows -> the US file's columns. Canada has no "some
# college, no credential" row, and its trades certificates sit where the US
# has some college: the pack's level words say so ("trades, college or CEGEP").
CERTIFICATE_COLUMN = {
    "No certificate, diploma or degree": "less_than_hs",
    "High (secondary) school diploma or equivalency certificate": "hs",
    "Apprenticeship or trades certificate or diploma": "some_college",
    "College, CEGEP or other non-university certificate or diploma": "associate",
    "University certificate or diploma below bachelor level": "associate",
    "Bachelor's degree": "bachelor",
    "University certificate or diploma above bachelor level": "master",
    "Master's degree": "master",
    "Degree in medicine, dentistry, veterinary medicine or optometry": "doctoral",
    "Earned doctorate": "doctoral",
}
ROLE_LEVELS = ("less_than_hs", "hs", "some_college", "associate", "bachelor", "master",
               "doctoral")
# The census reports NOC 00011-00015 (senior managers by sector) as one group.
NOC_CENSUS_GROUP = {code: "00018" for code in ("00011", "00012", "00013", "00014", "00015")}
# How much a title counts toward its occupation: a class title, an
# illustrative example, any other index title.
CLASS_WEIGHT, ILLUSTRATIVE_WEIGHT, INDEX_WEIGHT = 10, 5, 2
# Posting usage, as the US build's OVERRIDES: the occupation a posting means.
ROLE_OVERRIDES = {
    "account executive": "62100", "account manager": "62100",
    "customer success manager": "62100",          # as the US files it with sales
    "support engineer": "22221", "customer support engineer": "22221",
    "technical support specialist": "22221",      # NOC: wholesale technical sales
    "solutions engineer": "21311",
    "customer service representative": "64409",   # NOC: financial institutions
}


def _census_part(index: int, cache: Path | None) -> bytes:
    cached = cache / f"census_98100449_{index:02d}.csv" if cache else None
    if cached and cached.exists():
        print(f"  cached  {cached.name}")
        return cached.read_bytes()
    low = 1 + index * CENSUS_BATCH
    members = [[1], list(range(1, 17)), [5, 13], [1],
               list(range(low, min(low + CENSUS_BATCH, CENSUS_OCCUPATIONS + 1))), [2]]
    url = CENSUS_TABLE + "?" + urllib.parse.urlencode({
        "pid": "9810044901", "latestN": "5", "startDate": "", "endDate": "",
        "csvLocale": "en", "selectedMembers": str(members).replace(" ", ""),
        "checkedLevels": ""})
    print(f"  fetch   census occupations {low}-{low + CENSUS_BATCH - 1}")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    if cached:
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return data


def build_roles(cache: Path | None) -> tuple[list[list], list[list]]:
    """(education rows, title rows) in the US files' columns."""
    import re
    from collections import defaultdict

    from jsa.roles import normalize

    occupation_col = ("Occupation - Unit group - National Occupational Classification "
                      "(NOC) 2021 (821A)")
    certificate_col = "Highest certificate, diploma or degree (16)"
    counts: dict = defaultdict(lambda: defaultdict(float))
    totals: dict = defaultdict(float)
    names: dict = {}
    parts = (CENSUS_OCCUPATIONS + CENSUS_BATCH - 1) // CENSUS_BATCH
    for index in range(parts):
        text = _census_part(index, cache).decode("utf-8-sig")
        for row in csv.DictReader(io.StringIO(text)):
            found = re.match(r"(\d{5}) (.+)", row[occupation_col])
            if not found or row["VALUE"] == "":
                continue
            code, name = found.groups()
            names[code] = name
            certificate = row[certificate_col].replace("’", "'")
            value = float(row["VALUE"])        # 25-64 and 65+ rows add up
            if certificate.startswith("Total"):
                totals[code] += value
            elif certificate in CERTIFICATE_COLUMN:
                counts[code][CERTIFICATE_COLUMN[certificate]] += value
    education = []
    for code in sorted(names):
        held = sum(counts[code].values())
        if held <= 0:
            continue
        # The census rounds every count to 5: the rows meet the total closely.
        if abs(held - totals[code]) > max(50, 0.02 * totals[code]):
            raise SystemExit(f"census rows for {code} add to {held}, total {totals[code]}")
        education.append([code, names[code],
                          *[round(100 * counts[code][k] / held, 1) for k in ROLE_LEVELS],
                          "2021"])
    known = {row[0] for row in education}

    votes: dict = defaultdict(lambda: defaultdict(int))
    qualified: dict = defaultdict(lambda: defaultdict(int))

    def add(title: str, code: str, weight: int, table: dict) -> None:
        variants = [title]
        # "development and operations (DevOps) engineer" is also "DevOps engineer".
        short = re.search(r"\(([A-Za-z0-9-]+)\)", title)
        if short:
            variants.append(re.sub(r"[^()]*\(" + re.escape(short.group(1)) + r"\)",
                                   short.group(1), title, count=1))
        for variant in variants:
            if normalize(variant):
                table[normalize(variant)][code] += weight

    def noc_rows(name: str):
        text = fetch(NOC_URLS[name], cache).decode("utf-8-sig")
        return csv.DictReader(io.StringIO(text))

    for row in noc_rows("structure"):
        code = NOC_CENSUS_GROUP.get(row["Code - NOC 2021 V1.0"], row["Code - NOC 2021 V1.0"])
        if row["Level"] == "5" and code in known:
            add(row["Class title"], code, CLASS_WEIGHT, votes)
    for row in noc_rows("elements"):
        kind = row["Element Type Label English"]
        code = NOC_CENSUS_GROUP.get(row["Code - NOC 2021 V1.0"], row["Code - NOC 2021 V1.0"])
        if row["Level"] != "5" or code not in known or kind not in (
                "All examples", "Illustrative example(s)"):
            continue
        title = row["Element Description English"].strip()
        if " - " in title:
            # "technical support specialist - wholesale trade": normalize()
            # keeps the part before " - ", so it counts only where no plain
            # title exists, and only if every such title names one occupation.
            add(title, code, 1, qualified)
        else:
            add(title, code, ILLUSTRATIVE_WEIGHT if kind.startswith("Illustrative")
                else INDEX_WEIGHT, votes)
    titles = {t: min(b.items(), key=lambda kv: (-kv[1], kv[0])) for t, b in votes.items()}
    for title, by_code in qualified.items():
        if title not in titles and len(by_code) == 1:
            titles[title] = next(iter(by_code.items()))
    for title, code in ROLE_OVERRIDES.items():
        key = normalize(title)
        titles[key] = (code, titles.get(key, (code, CLASS_WEIGHT))[1])
    return education, [[t, c, w] for t, (c, w) in sorted(titles.items())]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("jsa/resources/data"))
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--only", choices=("points", "basemap", "roles"), default=None,
                    help="just the town, postal-area and outline files, the basemap, "
                         "or the education-by-occupation files")
    args = ap.parse_args()
    dest = args.out / "ca"
    dest.mkdir(parents=True, exist_ok=True)
    if args.only in (None, "roles"):
        education, titles = build_roles(args.cache)
        for name, payload, count in (
                ("role_education.csv.gz",
                 _csv(["soc", "occupation", *ROLE_LEVELS, "year"], education), len(education)),
                ("role_titles.csv.gz", _csv(["title", "soc", "weight"], titles), len(titles))):
            print(f"{name:22} {count:6} rows  sha256 {write_gz(dest / name, payload)}")
        print("Adapted from Statistics Canada, Table 98-10-0449-01 (2021 Census) and "
              "National Occupational Classification (NOC) 2021 Version 1.0, 2021.")
        if args.only == "roles":
            return
    if args.only != "points":
        raw = ground(args.cache)
        for name in TIERS:
            tier = basemap_tier(name, raw)
            file = f"ca_basemap_{name}.json.gz"
            digest = write_gz(dest / file,
                              json.dumps(tier, separators=(",", ":")).encode("utf-8"))
            for layer, feats in tier["layers"].items():
                print(f"  {name:7} {layer:7} {len(feats):6} features "
                      f"{sum(len(f[4]) // 2 for f in feats):8} points")
            print(f"{file:26} {(dest / file).stat().st_size / 1024:8.1f} KB  sha256 {digest}")
        if args.only == "basemap":
            print("Adapted from Statistics Canada, 2021 Census boundary files and "
                  "Road Network File, 2021. Contains information licensed under the "
                  "Open Government Licence - Canada (NRCan CanVec).")
            return

    places = build_places(query(9, "CSDNAME,CSDTYPE,PRUID,LANDAREA", POINT_OFFSET, args.cache),
                          query(7, "PCNAME,PRUID,LANDAREA", POINT_OFFSET, args.cache))
    fsa = build_fsa(query(14, "CFSAUID", POINT_OFFSET, args.cache))
    outline = build_outline(query(0, "PRUID", OUTLINE_OFFSET, args.cache))

    files = {
        "ca_places.csv.gz": (_csv(["name", "state", "lat", "lon"], places), len(places)),
        "ca_fsa.csv.gz": (_csv(["fsa", "lat", "lon"], fsa), len(fsa)),
        "ca_outline.json.gz": (json.dumps(outline, separators=(",", ":")).encode("utf-8"),
                               sum(len(v) for v in outline.values())),
    }
    for name, (payload, count) in files.items():
        digest = write_gz(dest / name, payload)
        print(f"{name:22} {count:6} rows  sha256 {digest}")
    print("Adapted from Statistics Canada, 2021 Census boundary files, 2021.")


if __name__ == "__main__":
    main()
