"""Rebuild the three shipped data files in data/ from their Census sources.

`data/` holds 700 KB of compressed binary that decides where every job
posting is placed on a map. Until this script existed there was no way for
anybody reading the repository to check it: the files had to be taken on
trust, which is not a thing to ask of somebody cloning a job-search tool.

Run it and diff:

    python tools/build_map_data.py --out data

It fetches three public-domain files from census.gov, rebuilds all three
shipped files, and prints the row count and SHA-256 of each. The gzip member
is written with mtime=0 so the bytes are reproducible: the same sources give
the same hash on any machine, any day.

This is a BUILD tool. Nothing in jsa/ imports it, and it is the only file in
the project allowed to open a socket for reference data -- tests/test_map.py
asserts that the drawing path cannot.

Sources, all US Census Bureau, all public domain (17 USC 105):
  2024 Gazetteer places  -> data/us_places.csv.gz   (town centroids)
  2024 Gazetteer ZCTAs   -> data/us_zips.csv.gz     (ZIP centroids)
  2023 cartographic
  boundaries, 1:20m      -> data/us_outline.json.gz (state outlines)
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
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


def build_places(archive: bytes) -> list[tuple[str, str, float, float]]:
    """name,state,lat,lon -- one row per town, biggest wins a shared name.

    A name repeats inside a state (Arkansas has two Salems). The larger by
    land area is the one a job posting means often enough that guessing the
    smaller would be wrong more often; both are a guess, and this one is
    written down. Nothing else in the tool assumes the choice.
    """
    best: dict[tuple[str, str], tuple[int, tuple[str, str, float, float]]] = {}
    for row in _tsv(archive):
        state, name, land = row[0], plain_name(row[3], row[4]), int(row[6])
        key = (name.lower(), state)
        found = best.get(key)
        if found is None or land > found[0]:
            best[key] = (land, (name, state, round(float(row[10]), 4),
                                round(float(row[11]), 4)))
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


def _shp_polygons(shp: bytes) -> list[list[list[tuple[float, float]]]]:
    """Every shape as a list of rings of (lon, lat). Polygons only (type 5)."""
    shapes, at = [], 100
    while at < len(shp):
        length = struct.unpack_from(">ii", shp, at)[1]
        body = at + 8
        kind = struct.unpack_from("<i", shp, body)[0]
        if kind != 5:
            raise SystemExit(f"shape type {kind} is not a polygon")
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
    parser.add_argument("--only", choices=("places", "zips", "outline"),
                        action="append", help="build just this one; repeatable")
    args = parser.parse_args(argv)

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    wanted = set(args.only or ("places", "zips", "outline"))
    report = []

    if "places" in wanted:
        print("us_places.csv.gz")
        rows = build_places(_only_member(fetch(PLACES_URL, args.cache), ".txt"))
        digest = write_gz(out / "us_places.csv.gz",
                          as_csv(("name", "state", "lat", "lon"), rows))
        report.append(("us_places.csv.gz", len(rows), digest))

    if "zips" in wanted:
        print("us_zips.csv.gz")
        rows = build_zips(_only_member(fetch(ZIPS_URL, args.cache), ".txt"))
        digest = write_gz(out / "us_zips.csv.gz",
                          as_csv(("zip", "lat", "lon"), rows))
        report.append(("us_zips.csv.gz", len(rows), digest))

    if "outline" in wanted:
        print("us_outline.json.gz")
        outline = build_outline(fetch(OUTLINE_URL, args.cache))
        payload = json.dumps(outline, separators=(",", ":"),
                             sort_keys=True).encode("utf-8")
        digest = write_gz(out / "us_outline.json.gz", payload)
        points = sum(len(ring) for rings in outline.values() for ring in rings)
        report.append(("us_outline.json.gz", points, digest))

    print()
    for name, count, digest in report:
        size = (out / name).stat().st_size
        print(f"{name:22} {count:>7} rows  {size / 1024:6.1f} KB  {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
