"""Rebuild the shipped role-education data in jsa/resources/data/ (plan 33).

Run by a maintainer, never at runtime. Writes:

  role_education.csv.gz  one row per occupation: the share of workers 25 and
                         older at each education level, from BLS Employment
                         Projections table 5.3 (American Community Survey).
  role_titles.csv.gz     normalized job title -> occupation code, from the
                         O*NET database's occupation titles, sample of reported
                         titles and job titles (CC BY 4.0, USDOL/ETA).

O*NET is downloaded. BLS answers scripts with 403 (checked 2026-10-08, even
with an identifying User-Agent), so its table is read from a file you save
from the published page in a browser:

  1. Open https://www.bls.gov/emp/tables/educational-attainment.htm
  2. In the browser console, run:
       copy([...document.querySelector("table").querySelectorAll("tbody tr")]
         .map(r => [...r.children].map(c => c.innerText.trim()
           .replace(/\\[\\d+\\]/g, "")).join("|")).join("\\n"))
  3. Paste into a text file and pass it with --bls.

Usage:
  python tools/build_role_data.py --bls bls_table_5_3.txt [--year 2023-24]
                                  [--onet-dir DIR] [--out jsa/resources/data]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsa.roles import EDUCATION_FILE, TITLES_FILE, normalize  # noqa: E402

ONET = "https://www.onetcenter.org/dl_files/database/db_31_0_csv/{name}.csv"
ONET_FILES = ("occupation_data", "sample_of_reported_titles", "job_titles")
# How much each kind of O*NET title counts when occupations share one: the
# occupation's own title most; a title workers reported, more when O*NET's
# own career site shows it under that occupation; a lay title by how many
# sources reported it. "Truck Driver" is a lay title of five occupations;
# four sources give it to heavy trucks and one each to the rest.
def weight(name: str, row: dict) -> int:
    if name == "occupation_data":
        return 10
    if name == "sample_of_reported_titles":
        return 3 + (2 if row.get("Shown in My Next Move") == "Y" else 0)
    return max(1, len([s for s in (row.get("Source(s)") or "").split(",") if s.strip()]))
# O*NET files these common titles under occupations whose people mostly do
# other work (checked on the owner's open postings, 2026-10-08): "account
# executive" under advertising and promotions managers, "site reliability
# engineer" under computer and information research scientists. Applied after
# the vote; a modification of the O*NET data, which the attribution says.
OVERRIDES = {
    "account executive": "41-3091",        # sales representatives of services
    "account manager": "41-3091",
    "customer success manager": "41-3091",
    "site reliability engineer": "15-1299",  # computer occupations, all other
}

LEVELS = ("less_than_hs", "hs", "some_college", "associate", "bachelor", "master",
          "doctoral")


def read_bls(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 9 or parts[1] == "00-0000":
            continue                       # the all-occupations total
        try:
            shares = [float(p) for p in parts[2:]]
        except ValueError:
            continue                       # a suppressed or footnoted row
        rows.append({"soc": parts[1], "occupation": parts[0],
                     **dict(zip(LEVELS, shares))})
    return rows


def onet_rows(name: str, folder: Path | None) -> list[dict]:
    if folder is not None:
        text = (folder / f"{name}.csv").read_text(encoding="utf-8")
    else:
        import httpx
        resp = httpx.get(ONET.format(name=name), timeout=60, headers={
            "User-Agent": "jobsearch-assistant build_role_data "
                          "(+https://github.com/MaxJ14251/jobsearch-assistant)"})
        resp.raise_for_status()
        text = resp.content.decode("utf-8")
        time.sleep(1)
    return list(csv.DictReader(io.StringIO(text)))


def titles(folder: Path | None, known: set[str]) -> dict[str, tuple[str, int]]:
    """normalized title -> (BLS code, weight). BLS codes are the first seven
    characters of an O*NET-SOC code ("15-1252.00" -> "15-1252")."""
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for name in ONET_FILES:
        column = {"occupation_data": "Title", "sample_of_reported_titles":
                  "Reported Job Title", "job_titles": "Job Title"}[name]
        for row in onet_rows(name, folder):
            soc = row["O*NET-SOC Code"][:7]
            title = normalize(row[column])
            if soc in known and title:
                votes[title][soc] += weight(name, row)
    # A remaining tie goes to the lower code, so a rebuild is stable.
    out = {title: min(by_soc.items(), key=lambda kv: (-kv[1], kv[0]))
           for title, by_soc in votes.items()}
    for title, soc in OVERRIDES.items():
        key = normalize(title)
        if soc in known:
            out[key] = (soc, out.get(key, (soc, 10))[1])
    return out


def write(path: Path, header: list[str], rows: list[list]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    # mtime=0: the same input gives the same bytes, so a rebuild with no
    # change in the sources shows no diff.
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buffer.getvalue().encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bls", type=Path, required=True,
                        help="table 5.3 rows, saved from the page (see above)")
    parser.add_argument("--year", default="2023-24", help="the table's survey years")
    parser.add_argument("--onet-dir", type=Path, default=None,
                        help="a folder with the three O*NET CSV files, instead of downloading")
    parser.add_argument("--out", type=Path, default=ROOT / "jsa" / "resources" / "data")
    args = parser.parse_args(argv)

    education = read_bls(args.bls)
    known = {r["soc"] for r in education}
    mapping = titles(args.onet_dir, known)
    write(args.out / EDUCATION_FILE, ["soc", "occupation", *LEVELS, "year"],
          [[r["soc"], r["occupation"], *[r[k] for k in LEVELS], args.year]
           for r in sorted(education, key=lambda r: r["soc"])])
    write(args.out / TITLES_FILE, ["title", "soc", "weight"],
          [[t, soc, w] for t, (soc, w) in sorted(mapping.items())])
    mapped = len({soc for soc, _ in mapping.values()})
    print(f"{EDUCATION_FILE}: {len(education)} occupations")
    print(f"{TITLES_FILE}: {len(mapping)} normalized titles, covering {mapped} of them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
