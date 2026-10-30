"""Testland (ZZ): a fictional country pack, for tests only.

It has everything a real pack has and shares nothing with the US: its own
provinces, postal codes, currency, phone format, paper and wording. A test
registers it (tests/test_testland.py), writes its two data files into a
temporary folder, and runs the app in it, so anything still assuming the US
shows up. ZZ is a code ISO 3166 leaves to users; no real country has it.
"""

from __future__ import annotations

from pathlib import Path

CODE = "ZZ"
NAME = "Testland"

STATES = {"NO": "Northshire", "SO": "Southmark"}
TERRITORIES: dict[str, str] = {}
SUBDIVISIONS = {**STATES, **TERRITORIES}
REGION_BY_NAME = {name.lower(): code for code, name in SUBDIVISIONS.items()}

PLACE_ALIASES = {"nville": ("Northville", "NO")}
FIRST_WORD: tuple = ()
COUNTRY_NOISE = r"^(testland)\s*[-–—,]\s*|\s*[-–—,]\s*(testland)$"
POSTAL_CODE = r"[A-Z]\d[A-Z] ?\d[A-Z]\d"
POSTAL_COLUMN = "code"


def postal_key(code: str) -> str:
    return (code or "").replace(" ", "").strip().upper()


IN_TEXT = "Testland"
ADJECTIVE = "Testland"
REGION_WORD = "province"
POSTAL_NAME = "postal code"
POSTAL_EXAMPLE = "a postal code like A1B 2C3"
PLACE_FORM = "City, PR"
SCORING_REGIONS = STATES
HOME_SIGNAL = r"\btestland\b"
ABROAD = r"\b(united states|usa|london|berlin|texas)\b"
REGION_WORDS = {"remote", "hybrid", "testland", "zz"}
TITLE_REGION_SUFFIX = r"\s*[-–—,]\s*(remote|testland)\b.*$"

CURRENCY = "TLD"
CURRENCY_SYMBOL = "T$"
HOURS_PER_YEAR = 1950
ANNUAL_BOUNDS = (20_000, 1_000_000)
HOURLY_BOUNDS = (10, 500)
PAY_LOW = 40_000
PAY_HIGH = 160_000

PAPER = "a4"
NUMERIC_DATE = "{month:02d}.{year}"

ROLE_EDUCATION = False

MAP_OFFSHORE: tuple = ()
MAP_PARALLELS = (40.0, 50.0)
MAP_ORIGIN = (45.0, 10.0)
MAP_MAINLAND = "the mainland"
MAP_OFFSHORE_NAMES = "the islands"

SOURCE_KINDS = frozenset({"testboard"})

REMOTE_LABEL = "Remote (Testland)"
AUTH_QUESTION = "Are you legally able to work in Testland?"
AUTH_EXAMPLE = "Testland citizen"
AUTHORIZED_KEY = "authorized_to_work_zz"

CONTACT_PHONE = r"\+99 \d{4} \d{4}"
CONTACT_POSTAL = r"\b[A-Z]\d[A-Z] ?\d[A-Z]\d\b"
CONTACT_CITY_REGION = r"\b([A-Z][a-z]+), (NO|SO)\b"
CONTACT_STREET = r"\b\d{1,5} [A-Z][a-z]+ (?:Lane|Close)\b"
# One fictional value per pattern: the privacy test removes each.
CONTACT_SAMPLE = {"phone": "+99 1234 5678", "postal": "A1B 2C3",
                  "street": "12 Heather Lane", "city_region": "Northville, NO"}

# Set by the test to a temporary folder holding the two files below.
DATA = Path("unset")
PLACES_FILE = DATA / "places.csv.gz"
POSTAL_FILE = DATA / "postal.csv.gz"
OUTLINE_FILE = DATA / "outline.json.gz"
SEED_COMPANIES = DATA / "companies.yaml"


def basemap_file(tier: str) -> Path:
    return DATA / f"basemap_{tier}.json.gz"


def use_folder(folder: Path) -> None:
    """Point the pack at `folder` and write its places and postal codes."""
    import csv
    import gzip

    global DATA, PLACES_FILE, POSTAL_FILE, OUTLINE_FILE, SEED_COMPANIES
    DATA = Path(folder)
    PLACES_FILE, POSTAL_FILE = DATA / "places.csv.gz", DATA / "postal.csv.gz"
    OUTLINE_FILE, SEED_COMPANIES = DATA / "outline.json.gz", DATA / "companies.yaml"
    with gzip.open(PLACES_FILE, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "state", "lat", "lon"])
        w.writerows([["Northville", "NO", 46.0, 10.0], ["Southport", "SO", 44.0, 10.5],
                     ["Lakeside", "NO", 46.1, 10.1]])
    with gzip.open(POSTAL_FILE, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "lat", "lon"])
        w.writerow(["A1B2C3", 46.0, 10.0])
