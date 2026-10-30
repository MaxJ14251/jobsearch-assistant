"""The United States pack.

The one table of US states (and the territory the place data covers), and
where the US data files live. Kept free of other jsa imports, so the build
tools can read the same table instead of keeping their own copy.
"""

from __future__ import annotations

from pathlib import Path

CODE = "US"
NAME = "United States"

# The fifty states and DC. Scoring has always used exactly these (its own
# copy said so: "All fifty states and DC").
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
# Puerto Rico is in the Census place data, so places can be resolved there;
# scoring's state bonus has never counted it.
TERRITORIES = {"PR": "Puerto Rico"}
# Every region a place can sit in: what place parsing and the map data use.
SUBDIVISIONS = {**STATES, **TERRITORIES}
REGION_BY_NAME = {name.lower(): code for code, name in SUBDIVISIONS.items()}

# --- places ---------------------------------------------------------------------
# Names a posting uses that the Census file does not. Each one is a place
# the Gazetteer knows under a different name, not a guess about a region.
PLACE_ALIASES = {
    "nyc": ("New York", "NY"),
    "new york city": ("New York", "NY"),
    "washington dc": ("Washington", "DC"),
    "washington d.c.": ("Washington", "DC"),
    "washington, d.c.": ("Washington", "DC"),
    "d.c.": ("Washington", "DC"),
    # The Census calls it "Urban Honolulu"; no posting ever will.
    "honolulu": ("Urban Honolulu", "HI"),
}

# The first word of a place name, abbreviated or not. The Census picks one
# ("St. Paul", "Mount Vernon") and a posting picks either, so both are
# indexed. Written as pairs so it works in both directions.
FIRST_WORD = (("st.", "saint"), ("st", "saint"), ("ste.", "sainte"),
              ("mt.", "mount"), ("ft.", "fort"))

# Country noise around the real text: "US - Schaumburg, United States".
COUNTRY_NOISE = (
    r"^(us|usa|u\.s\.|u\.s\.a\.|united states( of america)?)\s*[-–—,]\s*|"
    r"\s*[-–—,]\s*(usa|u\.s\.a\.|united states( of america)?)$")

# A home given as a ZIP code, and the key the ZIP file is read by.
POSTAL_CODE = r"\d{5}(-\d{4})?"
POSTAL_COLUMN = "zip"


def postal_key(code: str) -> str:
    """ZIP+4 and plain ZIP find the same point."""
    return (code or "").strip()[:5]


# The shipped data files (Census, Natural Earth, BLS and O*NET).
DATA = Path(__file__).resolve().parents[2] / "resources" / "data" / "us"
PLACES_FILE = DATA / "us_places.csv.gz"
POSTAL_FILE = DATA / "us_zips.csv.gz"
OUTLINE_FILE = DATA / "us_outline.json.gz"


def basemap_file(tier: str) -> Path:
    return DATA / f"us_basemap_{tier}.json.gz"
