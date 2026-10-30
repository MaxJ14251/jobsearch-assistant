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
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_map_data import _area, point_on_land, simplify, write_gz  # noqa: E402

SERVICE = ("https://geo.statcan.gc.ca/geo_wa/rest/services/2021/"
           "Cartographic_boundary_files/MapServer")
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


def query(layer: int, fields: str, offset: float, cache: Path | None) -> list[dict]:
    """Every feature of a layer, in pages, as {attributes, rings}."""
    out: list[dict] = []
    start = 0
    while True:
        params = {"where": "1=1", "outFields": fields, "returnGeometry": "true",
                  "outSR": "4326", "maxAllowableOffset": str(offset),
                  "geometryPrecision": "4", "orderByFields": "OBJECTID",
                  "resultOffset": str(start), "resultRecordCount": str(PAGE), "f": "json"}
        url = f"{SERVICE}/{layer}/query?{urllib.parse.urlencode(params)}"
        name = f"statcan_{layer}_{offset}_{start}.json"
        cached = cache / name if cache else None
        if cached and cached.exists():
            body = cached.read_bytes()
            print(f"  cached  {name}")
        else:
            print(f"  fetch   layer {layer}, from {start}")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read()
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(body)
        data = json.loads(body)
        if "error" in data:
            raise SystemExit(f"Statistics Canada refused the query: {data['error']}")
        feats = data.get("features") or []
        for f in feats:
            rings = [[(x, y) for x, y in ring] for ring in (f.get("geometry") or {}).get("rings", [])]
            if rings:
                out.append({"a": f["attributes"], "rings": rings})
        if len(feats) < PAGE and not data.get("exceededTransferLimit"):
            return out
        start += PAGE


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


def _csv(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path("jsa/resources/data"))
    ap.add_argument("--cache", type=Path, default=None)
    args = ap.parse_args()
    dest = args.out / "ca"
    dest.mkdir(parents=True, exist_ok=True)

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
