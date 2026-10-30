"""The Canada pack (ADR 0037).

Its provinces and territories, its postal areas and data files, and the
wording, pay, form and contact conventions a Canadian profile works in.
Kept free of other jsa imports, as the US pack is.

Measured before it was written (ADR 0037, 2026-10-30): where the place and
postal data come from and under what licence, which employer boards carry
Canadian postings, and what Canadian pay text looks like.
"""

from __future__ import annotations

from pathlib import Path

CODE = "CA"
NAME = "Canada"

# The ten provinces and three territories. Scoring's "your province" bonus
# counts all thirteen: a territory is as much a place to work as a province.
STATES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador", "NS": "Nova Scotia",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "NT": "Northwest Territories", "NU": "Nunavut",
    "YT": "Yukon",
}
TERRITORIES: dict[str, str] = {}
SUBDIVISIONS = {**STATES, **TERRITORIES}
REGION_BY_NAME = {name.lower(): code for code, name in SUBDIVISIONS.items()}
# The names postings also use: French, accented, older.
REGION_BY_NAME.update({"québec": "QC", "newfoundland": "NL", "labrador": "NL",
                       "pei": "PE", "yukon territory": "YT"})

# --- places ---------------------------------------------------------------------
# Names a posting uses that Statistics Canada's file does not. Toronto's
# pre-1998 cities are still how postings say where in Toronto (4 of the 578
# measured said "Scarborough, ON").
PLACE_ALIASES = {
    "scarborough": ("Toronto", "ON"),
    "etobicoke": ("Toronto", "ON"),
    "north york": ("Toronto", "ON"),
    "east york": ("Toronto", "ON"),
    "gta": ("Toronto", "ON"),
    "greater toronto area": ("Toronto", "ON"),
    "quebec city": ("Québec", "QC"),
    "québec city": ("Québec", "QC"),
    "ville de québec": ("Québec", "QC"),
    "greater vancouver": ("Vancouver", "BC"),
    "metro vancouver": ("Vancouver", "BC"),
    "kitchener-waterloo": ("Kitchener", "ON"),
}

FIRST_WORD = (("st.", "saint"), ("st", "saint"), ("ste.", "sainte"), ("ste", "sainte"),
              ("mt.", "mount"), ("ft.", "fort"))
# "Montreal" finds Montréal and "Levis" finds Lévis: postings drop accents.
FOLD_ACCENTS = True
# Where an alias may be cut from a town's own name. Not at a hyphen: in
# Canada that is half of every French name ("Saint-Jean-sur-Richelieu" must
# not answer to "Saint"). Bilingual "A / B" names are split by the build.
ALIAS_SPLIT = r"/"

COUNTRY_NOISE = (
    r"^(ca|can|canada)\s*[-–—,]\s*|"
    r"\s*[-–—,]\s*(ca|can|canada)$")

# A home given as a postal code: the full "M5V 2T6" or just its area "M5V".
# Only the area is ever kept or looked up (ADR 0037): setup stores the three
# characters, and the data holds nothing finer.
POSTAL_CODE = r"[A-Za-z]\d[A-Za-z](?:[ -]?\d[A-Za-z]\d)?"
POSTAL_COLUMN = "fsa"
POSTAL_AREA_ONLY = True


def postal_key(code: str) -> str:
    """"M5V 2T6", "m5v2t6" and "M5V" all find the area M5V."""
    return (code or "").strip().upper()[:3]


# --- scoring ----------------------------------------------------------------------
IN_TEXT = "Canada"
ADJECTIVE = "Canadian"
REGION_WORD = "province"
POSTAL_NAME = "postal code"
POSTAL_EXAMPLE = "a postal code like M5V 2T6 (only M5V is kept)"
PLACE_FORM = "City, PR"
SCORING_REGIONS = STATES

_PROVINCE_CODES = "ab|bc|mb|nb|nl|ns|nt|nu|on|pe|qc|sk|yt"
# Checked first: a location naming Canada or a province is home, whatever
# else it lists ("Remote (United States | Canada)" is open to a Canadian).
HOME_SIGNAL = (
    r"(\bcanada\b|\bcanadian\b|\bremote[ ,–-]*\(?ca\b|"
    r",\s*(?:" + _PROVINCE_CODES + r")(?![\w-])|"
    r"\b(?:alberta|british columbia|manitoba|new brunswick|newfoundland|"
    r"nova scotia|ontario|prince edward island|quebec|saskatchewan|"
    r"northwest territories|nunavut|yukon)\b)"
)
# Abroad from Canada: the United States first (its name, its states, its
# biggest job cities, and a state code after a comma, "Austin, TX"), then
# the rest of the world as the US pack lists it. "CA" is left out of the
# codes: it is California to one posting and Canada to the next. So are the
# codes that are also words after a comma ("Toronto, in office", "Ottawa, or
# remote"): Indiana, Oregon, Maine, Hawaii and Oklahoma are caught by name.
_US_CODES = ("al|ak|az|ar|co|ct|de|dc|fl|ga|id|il|ia|ks|ky|la|md|ma|mi|mn|"
             "ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|pa|ri|sc|sd|tn|tx|ut|vt|va|"
             "wa|wv|wi|wy")
ABROAD = (
    # "Remote (US)" is remote somewhere else: checked after HOME_SIGNAL, so
    # "Remote (US or Canada)" is still home.
    r"(\bremote[ ,–-]*\(?us\b|\(us\)|"
    r"\b(united states|usa|u\.s\.a?\.?|us only|us[- ]remote|"
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
    r"florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|"
    r"louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|"
    r"new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    r"rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
    r"virginia|washington|west virginia|wisconsin|wyoming|"
    r"nyc|san francisco|seattle|boston|chicago|austin|los angeles|denver|"
    r"atlanta|miami|dallas|houston|san diego|san jose|palo alto|mountain view|"
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
    r"england|scotland|wales|northern ireland|middle east|"
    r"emea|apac|latam|europe|asia)\b|"
    r",\s*(?:" + _US_CODES + r")(?![\w-]))"
)

REGION_WORDS = {
    "remote", "hybrid", "onsite", "on-site", "in office", "in-office",
    "canada", "ca", "can", "us", "usa", "u.s.", "uk", "emea", "apac", "latam",
    "europe", "americas", "amer", "north america", "middle east",
    "global", "worldwide", "anywhere", "gta",
}
TITLE_REGION_SUFFIX = r"\s*[-–—,]\s*(remote|canada|ca|emea|apac|latam)\b.*$"


# --- pay ----------------------------------------------------------------------
# Postings state pay in dollars, Canadian unless they say otherwise: "$100,000
# TO $140,000 CAD"; 73 of the 578 measured mention USD (ADR 0037).
CURRENCY = "CAD"
CURRENCY_SYMBOL = "$"
# A 40-hour year, as the US pack uses. Many Canadian employers work 37.5
# hours (1,950); not measured, so the US figure is kept rather than guessed.
HOURS_PER_YEAR = 2080
ANNUAL_BOUNDS = (20_000, 1_000_000)
# Minimum wage is $15-$17.75 an hour across provinces (2025-2026).
HOURLY_BOUNDS = (14, 500)
# The pay ranking's scale. Measured on the 203 yearly minimums in Canadian
# postings (ADR 0037): 5th percentile $55,000, 95th $215,040 -- the US band
# fits them, so it is kept.
PAY_LOW = 50_000
PAY_HIGH = 200_000

# --- distance -------------------------------------------------------------------
DISTANCE_UNIT = "km"
DISTANCE_UNITS = "km"
DISTANCE_SHORT = "km"
MILES_PER_UNIT = 0.621371192237334
# The US default of 40 miles, rounded to a slider step.
DEFAULT_RADIUS = 65

# --- resumes and letters --------------------------------------------------------
# Canada prints on US Letter. Numeric dates are written year first (the
# Canadian standard's order), which no reader can misread as day-month.
PAPER = "letter"
NUMERIC_DATE = "{year}-{month:02d}"

# --- who holds each kind of job -----------------------------------------------
# No Canadian education-by-occupation data ships yet: no line is shown.
ROLE_EDUCATION = False

# --- the national map -----------------------------------------------------------
# Canada Albers Equal Area Conic (ESRI:102001), the projection Canadian maps
# use. Nothing is drawn apart: the territories are part of the picture.
MAP_OFFSHORE: tuple = ()
MAP_PARALLELS = (50.0, 70.0)
MAP_ORIGIN = (40.0, -96.0)
MAP_MAINLAND = "Canada"
MAP_OFFSHORE_NAMES = "the North"

# --- job sources --------------------------------------------------------------
# No nationwide source (the owner's choice, ADR 0037): Job Bank's terms
# refuse automated access, and The Muse placed 0-23 of 100 postings in
# Canadian cities. Employer boards belong to no country and run here too.
SOURCE_KINDS: frozenset = frozenset()
# Canada's own employers: 22 confirmed boards with Canadian postings (ADR 0037).
SEED_COMPANIES = Path(__file__).resolve().parents[2] / "resources" / "companies_ca.yaml"

# --- forms --------------------------------------------------------------------
REMOTE_LABEL = "Remote (Canada)"
AUTH_QUESTION = "Are you legally entitled to work in Canada?"
AUTH_EXAMPLE = "Canadian citizen"
AUTHORIZED_KEY = "authorized_to_work_ca"

# --- contact details on a resume ----------------------------------------------
# Phones are the same North American plan as the US (+1), so the US pattern
# is this one too.
CONTACT_PHONE = r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
CONTACT_POSTAL = r"(?<![\w/.@-])[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d(?![\w/])"
CONTACT_CITY_REGION = (
    r"\b([A-ZÀ-Ý][A-Za-zÀ-ÿ.'-]+(?: [A-ZÀ-Ý][A-Za-zÀ-ÿ.'-]+){0,2}), "
    r"(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b")
CONTACT_STREET = (
    r"\b\d{1,6}[A-Za-z]? (?:[A-Za-zÀ-ÿ0-9.' -]{2,40}? (?:St|Street|Ave|Avenue|Rd|Road|"
    r"Blvd|Boulevard|Dr|Drive|Cres|Crescent|Cr|Court|Ct|Lane|Ln|Way|Pl|Place|Terr|"
    r"Terrace|Pkwy|Parkway|Circle|Gate|Line|Concession|Trail|Hwy|Highway)\b\.?|"
    r"(?:rue|avenue|boulevard|boul\.|chemin|ch\.|rang|place|montée) [A-Za-zÀ-ÿ0-9.' -]{2,40}?(?=,|$))"
    r"(?:,? (?:Apt|Unit|Suite|#|app\.|bureau) ?\w+)?")
# One fictional value per pattern (H0H 0H0 is the postal code Canada Post
# keeps for Santa Claus).
CONTACT_SAMPLE = {"phone": "(613) 555-0173", "postal": "H0H 0H0",
                  "street": "48 Birchwood Crescent", "city_region": "Barrie, ON"}

# The shipped data files (Statistics Canada, tools/build_ca_data.py).
DATA = Path(__file__).resolve().parents[2] / "resources" / "data" / "ca"
PLACES_FILE = DATA / "ca_places.csv.gz"
POSTAL_FILE = DATA / "ca_fsa.csv.gz"
OUTLINE_FILE = DATA / "ca_outline.json.gz"


def basemap_file(tier: str) -> Path:
    # No ground layer is built for Canada yet: the live map draws without it.
    return DATA / f"ca_basemap_{tier}.json.gz"
