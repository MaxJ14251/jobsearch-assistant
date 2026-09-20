"""Match scoring.

The score is deliberately simple and fully explainable — every point is
attributed to a reason string that ends up in `jobs.match_reasons` and on the
dashboard. No LLM call here: scoring thousands of listings with a model is slow,
expensive, and unauditable. The model's job starts at tailoring.

A job is *rejected* (score 0) only on hard signals: an excluded seniority in the
title, or a years-of-experience requirement well beyond the candidate's.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .config import Preferences
from .salary import Salary, from_row

# Fit: how well the role matches. These four sum to 1.0.
W_TITLE = 0.45
W_LOCATION = 0.25
W_KEYWORDS = 0.20
W_SENIORITY = 0.10

# The final score is W_FIT * fit + W_COMPENSATION * pay. See ADR 0006.
#
# Fit is scaled, not re-weighted, so every job whose pay is unknown keeps its
# exact order relative to every other: any movement in the ranking is caused
# by pay and nothing else, which is what makes a before/after diff explainable.
# Pay can move a score by at most +/-0.05 around an unknown job's -- less than
# one weak-versus-strong title match -- so a well-paid role you cannot credibly
# do does not outrank a good fit. Hard rejects run before any of this.
W_FIT = 0.90
W_COMPENSATION = 0.10

# The pay component rises linearly across this band of ANNUAL pay, on the
# posting's minimum (the likeliest entry-level offer, and immune to a senior
# level inflating the range). US figures, consistent with ADR 0002.
PAY_LOW = 50_000
PAY_HIGH = 200_000
# ADR 0001 decision 4: unknown pay is neutral, which means the MIDPOINT of the
# pay component. Zero would sink the ~half of postings that state no pay.
PAY_UNKNOWN = 0.5

_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:-\s*\d+\s*)?years?\b", re.I)

# Deliberately does NOT include "manager" or "architect": "Technical Account
# Manager" and "Solutions Architect" are target titles. Specific senior manager
# titles are handled by prefs.exclude_keywords instead.
_SENIOR_TITLE_RE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|distinguished|director|vp|"
    r"head\s+of|tech\s+lead|team\s+lead|engineering\s+lead|"
    r"(?:engineer|developer)\s+(?:iii|iv|v))\b",
    re.I,
)

# People-manager roles. The discriminator is where "Manager" sits: as the head
# noun ("Manager, Software Engineering" / "Manager of Support") it's a
# people-management job; trailing ("Technical Account Manager") it's an IC role
# and a legitimate target.
_PEOPLE_MANAGER_RE = re.compile(
    r"^\s*(?:senior\s+|sr\.?\s+)?manager\b|\bmanager\s+of\b|\bmanager\s*,",
    re.I,
)
_JUNIOR_HINT_RE = re.compile(
    r"\b(junior|jr\.?|entry[- ]level|associate|new ?grad|graduate|intern|i{1,2}\b|"
    r"early career|apprentice)\b",
    re.I,
)

# Fallback when the profile does not set job_search_preferences.max_years_experience.
DEFAULT_MAX_YEARS = 3


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+ ]", " ", (text or "").lower())


_TITLE_QUALIFIER_RE = re.compile(r"\s*[\(\[].*?[\)\]]\s*")


def dedup_key(company_id: int, title: str) -> str:
    """Collapse regional variants of the same req.

    Boards post one req per location: "Forward Deployed Engineer (Korea)",
    "... (West Coast)", "... (UK/Europe)". Those are one job to a human, so
    strip parenthetical qualifiers and any trailing location clause.
    """
    base = _TITLE_QUALIFIER_RE.sub(" ", title or "")
    base = re.sub(r"\s*[-–—,]\s*(remote|us|usa|emea|apac|latam)\b.*$", "", base, flags=re.I)
    return f"{company_id}:{' '.join(_norm(base).split())}"


def title_score(title: str, targets: list[str]) -> tuple[float, str | None]:
    t = _norm(title)
    best = 0.0
    best_target = None
    for target in targets:
        target_words = _norm(target).split()
        if not target_words:
            continue
        if _norm(target) in t:
            score = 1.0
        else:
            hits = sum(1 for w in target_words if w in t.split())
            # A single shared generic word ("Manager", "Engineer") is not a
            # match — "Manager, Globalization Production" is not a Technical
            # Account Manager. Require two words unless the target is one word.
            if hits < 2 and len(target_words) > 1:
                continue
            score = hits / len(target_words)
        if score > best:
            best, best_target = score, target
    return best, best_target


# A "remote" role is only useful if it is remote in the candidate's country.
# This build assumes US-based. Boards
# advertise plenty of "Remote - Singapore". Whitelisting every US city is
# hopeless, so: look for a positive US signal first, then for a foreign one.
# Only strong, unambiguous US markers — deliberately NOT two-letter state codes,
# which collide with country codes ("Berlin, DE" would read as Delaware).
_US_RE = re.compile(
    r"(\bunited states\b|\busa\b|\bu\.s\.a?\.?\b|\bus[- ]remote\b|"
    r"\bremote[ ,–-]*\(?us\b|\bus only\b)",
    re.I,
)
_NON_US_RE = re.compile(
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
    r"emea|apac|latam|europe|asia)\b",
    re.I,
)


def _fold(text: str) -> str:
    """Strip diacritics so an accented city still matches its plain spelling.

    "São Paulo" was scored as a US location: the pattern holds "sao paulo",
    and the feed writes the a with a tilde. Every rule that compares a location
    against a written-out name has this problem, so it is fixed once here
    rather than by spelling each city twice.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", text or "")
                   if not unicodedata.combining(c))


def is_non_us(location: str) -> bool:
    """True when a location names somewhere clearly outside the US."""
    loc = _fold(location)
    if _US_RE.search(loc):
        return False
    return bool(_NON_US_RE.search(loc))


# All fifty states and DC. The first version held nineteen, because those were
# the states the author's own search touched -- which is exactly the kind of
# thing that makes a tool work for one person and quietly misrank for everyone
# else.
_STATE_NAMES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "dc": "district of columbia", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana",
    "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan",
    "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire", "nj": "new jersey",
    "nm": "new mexico", "ny": "new york", "nc": "north carolina",
    "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon",
    "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}


def preferred_states(locations: list[str]) -> set[str]:
    """The states the reader's own location list names.

    "Austin, TX" gives {"tx"}; "Columbus, Ohio" gives {"oh"}. Used for the
    consolation prize below: your state, but not your city.
    """
    by_name = {name: abbr for abbr, name in _STATE_NAMES.items()}
    found: set[str] = set()
    for entry in locations or []:
        parts = [p.strip().lower() for p in str(entry).split(",")]
        for part in parts[1:] or parts:
            if part in _STATE_NAMES:
                found.add(part)
            elif part in by_name:
                found.add(by_name[part])
    return found


def in_state(location: str, state: str) -> bool:
    """Is this location in `state`? Matched on ", XX" or the full state name.

    A bare two-letter test would read "Berlin, DE" as Delaware. is_non_us()
    catches that first, and requiring the comma keeps the rest honest.

    `state` is accepted in any case. It used to be compared against a
    lowercased location without being lowercased itself, so `in_state(loc,
    "CA")` was always False and said nothing about why. Its one caller passes
    the lowercase form that `preferred_states` emits, so nothing was broken --
    which is precisely the shape of a function that breaks its next caller.
    """
    loc = _fold(location).lower()
    state = (state or "").strip().lower()
    full = _STATE_NAMES.get(state, state)
    return bool(re.search(r",\s*" + re.escape(state) + r"\b", loc)
                or re.search(r"\b" + re.escape(full) + r"\b", loc))


def _matches_city(location: str, pref: str) -> bool:
    """Is `location` in the preferred city?

    A bare substring test is wrong in a way that bites: "York, PA" matches
    inside "New York, NY". So require the city as a whole word AND the state
    to appear too — the state is what actually separates York from New York.

    That guard was then applied even to postings that name no state at all,
    and 27% of the author's tracker names none: 143 of 514, across 16 distinct
    strings, 117 of them one feed writing "San Francisco". Every one scored
    0.0, "outside your list" — the same as another state — for a reader whose
    own profile said "San Francisco, CA".

    So the state has to agree only when the posting states one. There is no
    ambiguity to resolve when it does not, and demanding a resolution throws
    the posting away. The residual risk is a bare "Columbus" that is Georgia
    reading as Ohio; the location is shown to the reader either way, and
    ranking a real posting slightly too high beats hiding it.
    """
    loc = _fold(location).lower()
    parts = [p.strip().lower() for p in _fold(pref).split(",")]
    city = parts[0]
    if not city or city.startswith("remote"):
        return False
    if not re.search(rf"\b{re.escape(city)}\b", loc):
        return False
    if len(parts) < 2:
        return True
    state = parts[1]
    full = _STATE_NAMES.get(state, state)
    if (re.search(rf"\b{re.escape(state)}\b", loc)
            or re.search(rf"\b{re.escape(full)}\b", loc)):
        return True
    return not names_a_state(loc)


def names_a_state(location: str) -> bool:
    """Does this location name a US state at all, by code or by name?"""
    loc = _fold(location).lower()
    return bool(
        any(re.search(rf",\s*{abbr}\b", loc) for abbr in _STATE_NAMES)
        or any(re.search(rf"\b{re.escape(name)}\b", loc)
               for name in _STATE_NAMES.values()))


def location_score(
    location: str, remote: str, prefs: Preferences
) -> tuple[float, str]:
    loc = (location or "").lower()
    if is_non_us(location):
        return 0.0, f"location {location!r} is outside the US"
    if remote == "remote":
        if any("remote" in p.lower() for p in prefs.locations):
            return 1.0, "remote role and remote is acceptable"
        return 0.7, "remote role"
    for pref in prefs.locations:
        if _matches_city(loc, pref):
            return 1.0, f"located in {pref}"
    # Your state, but not your city: worth something, not everything. This
    # used to be a hardcoded California bonus, which ranked the author's home
    # state above a reader's own city.
    for state in sorted(preferred_states(prefs.locations)):
        if in_state(location, state):
            return 0.6, f"in {_STATE_NAMES[state].title()}, a state you listed"
    if not loc:
        return 0.4, "location not stated"
    return 0.0, f"location {location!r} is outside your list"


def keyword_score(description: str, have: list[str]) -> tuple[float, list[str]]:
    if not have:
        return 0.0, []
    blob = _norm(description)
    matched = [k for k in have if _norm(k) and _norm(k) in blob]
    # 6 overlapping keywords is a strong signal; more adds nothing.
    return min(len(matched) / 6.0, 1.0), matched


def required_years(description: str) -> int | None:
    """Largest 'N years' requirement mentioned, if any."""
    values = [int(m.group(1)) for m in _YEARS_RE.finditer(description or "")]
    values = [v for v in values if v <= 30]
    return max(values) if values else None


def pay_score(pay: Salary | None) -> tuple[float, str]:
    """The pay component in 0..1, and why."""
    if pay is None:
        return PAY_UNKNOWN, "pay not stated (scored neutral)"
    annual = pay.annual_minimum()
    value = (annual - PAY_LOW) / (PAY_HIGH - PAY_LOW)
    value = min(max(value, 0.0), 1.0)
    note = f"pays {pay.label()}"
    if pay.period == "hour":
        note += f" (about ${annual:,}/yr)"
    elif pay.minimum != pay.maximum:
        note += " (ranked on the low end)"
    return value, note


def job_region(location: str, regions: dict[str, list[str]]) -> str | None:
    """The first of the profile's regions whose places appear in `location`."""
    loc = (location or "").lower()
    for name, places in (regions or {}).items():
        if any(str(p).lower() in loc for p in places or []):
            return name
    return None


def score_job(job: dict[str, Any], prefs: Preferences) -> tuple[float, list[str]]:
    """Return (score in 0..1, reasons). Score 0 means rejected.

    Reads pay from job["salary_min"/"salary_max"/"salary_period"], which the
    caller fills from jsa.salary.extract. Absent means unknown, never zero.
    """
    title = job.get("title") or ""
    description = job.get("description") or ""
    reasons: list[str] = []

    # --- hard rejects --------------------------------------------------
    for bad in prefs.exclude_keywords:
        if _norm(bad) and _norm(bad) in _norm(title):
            return 0.0, [f"rejected: title contains {bad!r}"]

    if _SENIOR_TITLE_RE.search(title) and not _JUNIOR_HINT_RE.search(title):
        return 0.0, [f"rejected: {title!r} reads as a senior/lead title"]

    if _PEOPLE_MANAGER_RE.search(title):
        return 0.0, [f"rejected: {title!r} is a people-management role"]

    ceiling = prefs.max_years_experience
    years = required_years(description)
    if years is not None and years > ceiling:
        return 0.0, [f"rejected: requires {years}+ years of experience"]

    if is_non_us(job.get("location") or ""):
        return 0.0, [f"rejected: {job.get('location')!r} is outside the US"]

    # The floor is a threshold the operator opted into (ADR 0001 decision 7).
    # A job is rejected only when even its TOP figure is below it; unknown pay
    # is never rejected (decision 4).
    pay = from_row(job)
    floor_setting = prefs.compensation_floor
    if pay is not None and floor_setting is not None:
        region = job_region(job.get("location") or "", prefs.regions)
        floor = floor_setting.for_region(region)
        if floor is not None and pay.annual_maximum() < floor:
            where = f" for {region}" if region else ""
            return 0.0, [f"rejected: pays at most ${pay.annual_maximum():,}/yr, "
                         f"below your floor of ${floor:,}{where}"]

    # --- weighted signals ----------------------------------------------
    # Engineering titles are tier 1. Only if none match do we consider the
    # sales-track fallbacks, and those get scaled down at the end so an
    # engineering role always outranks a sales role of equivalent quality.
    track = "engineering"
    t_score, t_match = title_score(title, prefs.target_titles)
    if t_score == 0 and prefs.fallback_titles:
        f_score, f_match = title_score(title, prefs.fallback_titles)
        if f_score > 0:
            track, t_score, t_match = "sales", f_score, f_match

    if t_match and t_score >= 0.5:
        label = f"title matches {t_match!r} ({t_score:.0%})"
        if track == "sales":
            label += " — sales track, ranked below engineering"
        reasons.append(label)
    elif t_score > 0:
        reasons.append(f"weak title overlap with {t_match!r}")
    else:
        reasons.append("title does not match any target role")

    l_score, l_reason = location_score(
        job.get("location") or "", job.get("remote") or "unknown", prefs
    )
    reasons.append(l_reason)

    k_score, matched = keyword_score(description, prefs.have_keywords)
    if matched:
        reasons.append("JD mentions " + ", ".join(matched[:6]))
    else:
        reasons.append("no overlap with your keyword bank")

    if _JUNIOR_HINT_RE.search(title):
        s_score = 1.0
        reasons.append("explicitly entry-level / junior")
    elif years is not None:
        s_score = 1.0 if years <= ceiling else 0.0
        reasons.append(f"asks for {years} years")
    else:
        s_score = 0.5
        reasons.append("seniority unstated")

    p_score, p_reason = pay_score(pay)
    reasons.append(p_reason)

    fit = (
        W_TITLE * t_score
        + W_LOCATION * l_score
        + W_KEYWORDS * k_score
        + W_SENIORITY * s_score
    )
    if track == "sales":
        fit *= prefs.fallback_weight
    # The sales discount applies to fit only. Applied to the whole score it
    # also scaled the neutral pay term, and a sales job and an engineering job
    # that both state no pay could swap places -- a reordering caused by
    # nothing. Found by test_unknown_pay_keeps_the_old_order.
    score = W_FIT * fit + W_COMPENSATION * p_score
    return round(score, 3), reasons


def job_track(title: str, prefs: Preferences) -> str:
    """'engineering' or 'sales' — which title tier this role came from."""
    if title_score(title, prefs.target_titles)[0] > 0:
        return "engineering"
    if prefs.fallback_titles and title_score(title, prefs.fallback_titles)[0] > 0:
        return "sales"
    return "engineering"
