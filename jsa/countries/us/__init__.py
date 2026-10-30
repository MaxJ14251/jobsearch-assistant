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

# The shipped data files (Census, Natural Earth, BLS and O*NET).
DATA = Path(__file__).resolve().parents[2] / "resources" / "data" / "us"
PLACES_FILE = DATA / "us_places.csv.gz"
POSTAL_FILE = DATA / "us_zips.csv.gz"
OUTLINE_FILE = DATA / "us_outline.json.gz"


def basemap_file(tier: str) -> Path:
    return DATA / f"us_basemap_{tier}.json.gz"
