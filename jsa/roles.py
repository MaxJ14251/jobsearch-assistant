"""Who holds each kind of job, nationwide (plan 33, ADR 0033).

A posting's title is matched to an occupation (O*NET titles, USDOL/ETA, CC BY
4.0), and the occupation carries the education of the people who hold it
(BLS Employment Projections table 5.3, from the American Community Survey).
That is what workers in the role hold, not what the job requires, and not
who any company hires; every surface says so.

Both tables ship in jsa/resources/data/ (tools/build_role_data.py makes
them), so this needs no network. When a title doesn't match confidently,
the answer is None, never a guess.
"""

from __future__ import annotations

import csv
import functools
import gzip
import io
import re
from dataclasses import dataclass

from .config import DATA_DIR

EDUCATION_FILE = "role_education.csv.gz"
TITLES_FILE = "role_titles.csv.gz"
# A title that isn't in the table word for word is matched on its words;
# below this share of shared words it is left unmatched. Measured on the
# owner's open titles (ADR 0033): at 0.6, matches sharing two words of three
# ("Thermal Engineer" -> boiler operators) were wrong more often than right.
MIN_CONFIDENCE = 0.7
# A title that ends with a known title of two or more words.
SUFFIX_CONFIDENCE = 0.85
CAVEAT = "People in this role nationwide, not this company's hires."
ATTRIBUTION = ("Education of workers 25 and older by occupation: U.S. Bureau of Labor "
               "Statistics, Employment Projections table 5.3 (American Community Survey "
               "{year}). Job titles: O*NET 31.0 Database by the U.S. Department of Labor, "
               "Employment and Training Administration (USDOL/ETA), used under the CC BY "
               "4.0 license; this tool has modified it (titles normalized and mapped to "
               "BLS occupations), and USDOL/ETA has not approved, endorsed, or tested "
               "these modifications. O*NET® is a trademark of USDOL/ETA.")
SOURCE_URLS = {
    "bls": "https://www.bls.gov/emp/tables/educational-attainment.htm",
    "onet": "https://www.onetcenter.org/database.html",
    "license": "https://creativecommons.org/licenses/by/4.0/",
}

# Words that say how senior, which level, or where, never which occupation.
# ("Assistant", "associate", "chief" and "head" stay: they are part of
# occupations such as medical assistant, sales associate, chief executive.)
_DROP = {
    "senior", "sr", "junior", "jr", "lead", "staff", "principal",
    "entry", "level", "mid", "early", "career", "new", "grad",
    "graduate", "intern", "internship", "co-op", "coop", "trainee", "apprentice",
    "i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5", "remote", "hybrid", "onsite",
    "part-time", "full-time",
    "the", "a", "an", "of", "and", "&", "for", "to", "in", "at", "with",
}
# (Not "contract", "full" or "part": "Contract Manager", "Full Stack Engineer".)
# Common short forms in posting titles.
_EXPAND = {
    "eng": "engineer", "engr": "engineer", "dev": "developer", "devs": "developers",
    "mgr": "manager", "mgmt": "management", "rep": "representative",
    "reps": "representatives", "admin": "administrator", "svc": "service",
    "ops": "operations", "swe": "software engineer", "sre": "site reliability engineer",
    "qa": "quality assurance", "it": "it", "hr": "human resources",
    "csm": "customer success manager", "ae": "account executive",
    "sdr": "sales development representative", "bdr": "business development representative",
}


def normalize(title: str | None) -> str:
    """'Sr. Software Engineer II (Starlink) - Remote' -> 'software engineer'."""
    text = (title or "").lower()
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    # The occupation comes first: "Account Executive, Mid-Market", "Software
    # Engineer - Platform", "Support Engineer | Tier 2".
    text = re.split(r"\s[-–—|/]\s|,|:|\s@\s", text)[0]
    words = []
    for word in re.findall(r"[a-z0-9+#&'-]+", text.replace("'s", "")):
        word = word.strip("-'")
        for part in _EXPAND.get(word, word).split():
            if part and part not in _DROP:
                # Singular, on both sides: O*NET names occupations in the
                # plural ("Electricians"), postings in the singular, and a
                # mismatch sent "electrician" to a helpers' lay title.
                words.append(_singular(part))
    return " ".join(words)


# Words that end in "s" without being plural.
_NOT_PLURAL = {"sales", "business", "analytics", "logistics", "physics", "economics",
               "mathematics", "statistics", "graphics", "electronics", "mechanics",
               "robotics", "news", "series", "operations", "systems", "services"}


def _singular(word: str) -> str:
    if word in _NOT_PLURAL:
        return word
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _tokens(normalized: str) -> frozenset[str]:
    return frozenset(_singular(w) for w in normalized.split())


@dataclass(frozen=True)
class Occupation:
    soc: str
    name: str
    confidence: float
    matched: str            # the table's title it was matched to


@dataclass(frozen=True)
class Mix:
    soc: str
    name: str
    year: str
    hs_or_less: float       # shares, 0..1
    some_college_or_associate: float
    bachelors: float
    graduate: float

    def line(self) -> str:
        pct = lambda v: f"{round(100 * v)}%"
        return (f"{self.name} ({self.soc}): {pct(self.hs_or_less)} high school or less · "
                f"{pct(self.some_college_or_associate)} some college or associate's · "
                f"{pct(self.bachelors)} bachelor's · {pct(self.graduate)} graduate degree.")


def _rows(name: str):
    path = DATA_DIR / name
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def available() -> bool:
    return (DATA_DIR / EDUCATION_FILE).is_file() and (DATA_DIR / TITLES_FILE).is_file()


@functools.lru_cache(maxsize=1)
def _education() -> dict[str, Mix]:
    out = {}
    for r in _rows(EDUCATION_FILE):
        f = {k: float(r[k]) / 100 for k in ("less_than_hs", "hs", "some_college",
                                            "associate", "bachelor", "master", "doctoral")}
        out[r["soc"]] = Mix(r["soc"], r["occupation"], r["year"], f["less_than_hs"] + f["hs"],
                            f["some_college"] + f["associate"], f["bachelor"],
                            f["master"] + f["doctoral"])
    return out


@functools.lru_cache(maxsize=1)
def _titles() -> tuple[dict[str, tuple[str, int]], dict[str, list[str]]]:
    """(normalized title -> (soc, weight), word -> titles containing it)."""
    exact: dict[str, tuple[str, int]] = {}
    index: dict[str, list[str]] = {}
    for r in _rows(TITLES_FILE):
        exact[r["title"]] = (r["soc"], int(r["weight"]))
        for word in _tokens(r["title"]):
            index.setdefault(word, []).append(r["title"])
    return exact, index


def occupation_for(title: str | None) -> Occupation | None:
    """The occupation a posting title names, or None when unsure."""
    if not available():
        return None
    wanted = normalize(title)
    if not wanted:
        return None
    exact, index = _titles()
    names = _education()
    if wanted in exact:
        soc = exact[wanted][0]
        return Occupation(soc, names[soc].name, 1.0, wanted) if soc in names else None
    # Words in front of a known title narrow it: "Strategic Account
    # Executive", "Backend Software Engineer". The longest known title (two
    # words or more) the posting's title ends with is the occupation.
    parts = wanted.split()
    for start in range(1, len(parts) - 1):
        tail = " ".join(parts[start:])
        if tail in exact and exact[tail][0] in names:
            soc = exact[tail][0]
            return Occupation(soc, names[soc].name, SUFFIX_CONFIDENCE, tail)
    words = _tokens(wanted)
    head = _singular(wanted.split()[-1])
    best: tuple[float, int, str] | None = None
    # Only titles that end in the same word: "engineer", "representative".
    # The last word names the occupation; the others narrow it.
    for candidate in index.get(head, []):
        if _singular(candidate.split()[-1]) != head:
            continue
        theirs = _tokens(candidate)
        score = len(words & theirs) / len(words | theirs)
        weight = exact[candidate][1]
        if best is None or (score, weight) > best[:2]:
            best = (score, weight, candidate)
    if best is None or best[0] < MIN_CONFIDENCE:
        return None
    soc = exact[best[2]][0]
    return Occupation(soc, names[soc].name, round(best[0], 3), best[2]) if soc in names else None


def education_mix(soc: str | None) -> Mix | None:
    if not soc or not available():
        return None
    return _education().get(soc)


def for_title(title: str | None) -> dict | None:
    """What a page shows: the mix line, the caveat and the sources, or None."""
    occupation = occupation_for(title)
    mix = education_mix(occupation.soc) if occupation else None
    if mix is None:
        return None
    return {"soc": mix.soc, "occupation": mix.name, "line": mix.line(), "year": mix.year,
            "caveat": CAVEAT, "confidence": occupation.confidence,
            "attribution": ATTRIBUTION.format(year=mix.year), "sources": SOURCE_URLS}
