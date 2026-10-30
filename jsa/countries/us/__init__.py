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


# --- pay ----------------------------------------------------------------------
# Postings state pay in dollars: "$90,000 - $120,000", "Zone 1: $98,700 USD".
CURRENCY = "USD"
CURRENCY_SYMBOL = "$"
HOURS_PER_YEAR = 2080
# What a plausible figure is for its period, in dollars (ADR 0006).
ANNUAL_BOUNDS = (20_000, 1_000_000)
HOURLY_BOUNDS = (10, 500)
# The pay ranking's scale: a yearly minimum at or under PAY_LOW scores 0, at
# or over PAY_HIGH scores 1. US figures; another country needs its own.
PAY_LOW = 50_000
PAY_HIGH = 200_000

# --- the national map -----------------------------------------------------------
# Not drawn on the national map, and counted in words instead. Alaska and
# Hawaii at their true position turn the lower 48 into a thumbnail, and an
# inset is a second scale on one picture. Measured 2026-09-27: 0 of 998
# unreviewed postings place in either, so an inset would be decoration.
MAP_OFFSHORE = ("AK", "HI", "PR", "VI", "GU", "MP", "AS")
# Albers equal-area conic for the United States: the parameters everybody
# uses, so the shape is the one people recognise. (standard parallels, and
# the origin as (latitude, longitude))
MAP_PARALLELS = (29.5, 45.5)
MAP_ORIGIN = (37.5, -96.0)

# --- job sources --------------------------------------------------------------
# Sources that search one country, asked about the operator's own cities:
# USAJOBS is federal US jobs; The Muse's listings are overwhelmingly US. A
# kind another country's pack names is skipped when this one is not working.
# Employer boards (greenhouse, lever, ashby, workday...) belong to no country.
SOURCE_KINDS = frozenset({"themuse", "usajobs"})
# The starter list of employers a new install copies (resources/companies.yaml).
SEED_COMPANIES = Path(__file__).resolve().parents[2] / "resources" / "companies.yaml"

# --- forms --------------------------------------------------------------------
# The locations entry that means "remote, in this country": setup's checkbox
# writes it, and scoring treats any "remote" entry as accepting remote roles.
REMOTE_LABEL = "Remote (US)"
# The work-authorization question forms ask, and an example answer.
AUTH_QUESTION = "Are you authorized to work in the US?"
AUTH_EXAMPLE = "US citizen"
# The profile's optional yes/no for that question (the extension reads it;
# it is never inferred from the free-text work_authorization).
AUTHORIZED_KEY = "authorized_to_work_us"

# --- contact details on a resume ----------------------------------------------
# What a resume import finds and removes before any text reaches the model.
# Missing a format here is a privacy leak, not just a parsing miss: the
# import also applies every other country's patterns, so a resume from
# elsewhere is still cleaned.
CONTACT_PHONE = r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
# Not inside a word or link: a handle such as github.com/name12345 has five
# digits too (found on the first real run).
CONTACT_POSTAL = r"(?<![\w/.@-])\d{5}(?:-\d{4})?(?![\w/])"
CONTACT_CITY_REGION = r"\b([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){0,2}), ([A-Z]{2})\b"
CONTACT_STREET = (
    r"\b\d{1,6} [A-Za-z0-9.' ]{2,40}? (?:St|Street|Ave|Avenue|Rd|Road|Blvd|"
    r"Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Ter|Terrace|Pkwy|"
    r"Circle|Cir)\b\.?(?:,? (?:Apt|Unit|Suite|#) ?\w+)?")

# The shipped data files (Census, Natural Earth, BLS and O*NET).
DATA = Path(__file__).resolve().parents[2] / "resources" / "data" / "us"
PLACES_FILE = DATA / "us_places.csv.gz"
POSTAL_FILE = DATA / "us_zips.csv.gz"
OUTLINE_FILE = DATA / "us_outline.json.gz"


def basemap_file(tier: str) -> Path:
    return DATA / f"us_basemap_{tier}.json.gz"
