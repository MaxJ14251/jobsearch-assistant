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


# --- scoring ----------------------------------------------------------------------
# What the reason lines call the country and its regions.
IN_TEXT = "the US"
REGION_WORD = "state"
# The regions scoring's "your state, not your city" bonus counts.
SCORING_REGIONS = STATES

# A "remote" role is only useful if it is remote in the candidate's country.
# Boards advertise plenty of "Remote - Singapore". Whitelisting every US city
# is hopeless, so: look for a positive US signal first, then for a foreign one.
# Only strong, unambiguous US markers — deliberately NOT two-letter state codes,
# which collide with country codes ("Berlin, DE" would read as Delaware).
HOME_SIGNAL = (
    r"(\bunited states\b|\busa\b|\bu\.s\.a?\.?\b|\bus[- ]remote\b|"
    r"\bremote[ ,–-]*\(?us\b|\bus only\b)"
)
ABROAD = (
    r"\b(canada|toronto|vancouver|montreal|ottawa|"
    r"u\.?k\.?|united kingdom|london|manchester|edinburgh|"
    r"ireland|dublin|france|paris|germany|berlin|munich|"
    r"netherlands|amsterdam|spain|madrid|barcelona|portugal|lisbon|"
    r"italy|rome|milan|switzerland|zurich|geneva|austria|vienna|"
    r"belgium|brussels|sweden|stockholm|norway|oslo|denmark|copenhagen|"
    r"finland|helsinki|poland|warsaw|czech|prague|hungary|budapest|"
    r"romania|bucharest|greece|athens|turkey|istanbul|ukraine|kyiv|"
    r"israel|tel aviv|uae|dubai|abu dhabi|saudi|qatar|doha|egypt|cairo|"
    r"india|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|"
    r"china|beijing|shanghai|shenzhen|hong kong|taiwan|taipei|"
    r"japan|tokyo|osaka|korea|seoul|singapore|malaysia|kuala lumpur|"
    r"indonesia|jakarta|thailand|bangkok|vietnam|hanoi|philippines|manila|"
    r"australia|sydney|melbourne|new zealand|auckland|"
    r"brazil|sao paulo|mexico|argentina|buenos aires|chile|santiago|"
    r"colombia|bogota|peru|lima|costa rica|"
    r"south africa|nigeria|lagos|kenya|nairobi|"
    # Counted in the author's tracker: "England - Cambridge" and "Middle East"
    # were being scored as US locations, because the list held "united kingdom"
    # but not the constituent countries, and no region names below continent
    # size.
    r"england|scotland|wales|northern ireland|middle east|"
    r"emea|apac|latam|europe|asia)\b"
)

# Words that say where, or how, and never name a team: a title's "(US)" or
# "(EMEA)" qualifier. A foreign country or city is caught by ABROAD; a state
# by its name or code.
REGION_WORDS = {
    "remote", "hybrid", "onsite", "on-site", "in office", "in-office",
    "us", "usa", "u.s.", "uk", "emea", "apac", "latam", "europe", "americas",
    "amer", "north america", "east coast", "west coast", "middle east",
    "global", "worldwide", "anywhere", "bay area", "sf bay area",
}
# A title's trailing " - Remote", ", US": a regional copy of one req.
TITLE_REGION_SUFFIX = r"\s*[-–—,]\s*(remote|us|usa|emea|apac|latam)\b.*$"


# The shipped data files (Census, Natural Earth, BLS and O*NET).
DATA = Path(__file__).resolve().parents[2] / "resources" / "data" / "us"
PLACES_FILE = DATA / "us_places.csv.gz"
POSTAL_FILE = DATA / "us_zips.csv.gz"
OUTLINE_FILE = DATA / "us_outline.json.gz"


def basemap_file(tier: str) -> Path:
    return DATA / f"us_basemap_{tier}.json.gz"
