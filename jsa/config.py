"""Paths and configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent   # a clone, or site-packages
# What ships with the code, read-only: the schema, the map data, the seed
# companies list, the example profile and .env template (plan 27).
RESOURCES = Path(__file__).resolve().parent / "resources"


def running_from_clone(root: Path = ROOT) -> bool:
    """A git checkout or an unpacked source tree, not an installed wheel."""
    return (root / ".git").exists() or (root / "pyproject.toml").exists()


def _home() -> Path:
    """Where the person's own files live: profile, .env, tracker, output,
    backups, logs. JSA_HOME if set; else the clone itself, so a clone keeps
    today's layout; else a per-user folder."""
    if os.environ.get("JSA_HOME"):
        return Path(os.environ["JSA_HOME"]).expanduser()
    if running_from_clone():
        return ROOT
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "jsa"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "jsa"


HOME = _home()
ENV_PATH = HOME / ".env"


def load_dotenv(path: Path = ENV_PATH) -> list[str]:
    """Load KEY=value pairs from .env into the environment.

    Deliberately hand-rolled rather than pulling in python-dotenv — it's a
    dozen lines and keeps the dependency list honest.

    A real environment variable always wins over the file, so an export in the
    shell is never silently overridden by a stale .env. Returns the names it
    set, so `jsa env` can report what came from where without printing values.
    """
    if not path.exists():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value or value.startswith("<"):
            continue          # skip unfilled placeholders like <paste-key-here>
        if os.environ.get(key):
            continue          # the real environment takes precedence
        os.environ[key] = value
        loaded.append(key)
    return loaded


PROFILE_PATH = HOME / "profile" / "master_profile.yaml"
EXAMPLE_PROFILE = RESOURCES / "master_profile.example.yaml"
ENV_EXAMPLE = RESOURCES / ".env.example"
SCHEMA_PATH = RESOURCES / "schema.sql"
DATA_DIR = RESOURCES / "data"
SEED_COMPANIES = RESOURCES / "companies.yaml"
# The companies list is both shipped and yours: `jsa verify --write` edits it.
# A clone edits the tracked file itself, as before; an installed copy gets its
# own copy in HOME, seeded on first use, and the package is never written.
COMPANIES_PATH = SEED_COMPANIES if HOME == ROOT else HOME / "config" / "companies.yaml"
DB_PATH = Path(os.environ.get("JSA_DB", HOME / "jobsearch.db"))
OUTPUT_DIR = HOME / "output"

PROJECT_URL = "https://github.com/MaxJ14251/jobsearch-assistant"


def user_agent() -> str:
    """User-Agent sent to job boards: the tool's name and its project link.

    A contact address is added only when JSA_CONTACT_EMAIL is set. The
    profile's email is never used here (owner's decision, 2026-10-23): no
    board's recorded terms ask for a contact, the project link already names
    the client, and the profile email went to every board on every run.
    USAJOBS is separate: its terms require USAJOBS_EMAIL, sent only there.
    """
    contact = os.environ.get("JSA_CONTACT_EMAIL", "").strip()
    suffix = f"; contact {contact}" if contact else ""
    from . import __version__
    return f"jobsearch-assistant/{__version__} (+{PROJECT_URL}{suffix})"


REQUEST_TIMEOUT = 20.0
POLITE_DELAY_S = 1.0  # between requests to the same host


class ConfigError(RuntimeError):
    """Raised when configuration is missing or contains an unresolved TODO."""


NO_FLOOR = "no_floor"


# The pay floor's profile key, in the working country's currency (one country
# per profile). Before countries it was compensation_floor_usd, which is still
# read; a profile may not set both.
FLOOR_KEY = "compensation_floor"
OLD_FLOOR_KEY = "compensation_floor_usd"


def floor_setting(prefs: dict[str, Any]) -> tuple[Any, str]:
    """(the floor as written, the key it is under). A key present but empty
    reads as unset, so a profile mid-rename still works."""
    new, old = prefs.get(FLOOR_KEY), prefs.get(OLD_FLOOR_KEY)
    if new is not None and old is not None:
        raise ConfigError(
            f"master_profile.yaml: both {FLOOR_KEY} and {OLD_FLOOR_KEY} are set. Keep "
            f"{FLOOR_KEY} (the same figure, in your country's currency) and delete "
            f"{OLD_FLOOR_KEY}.")
    if new is not None:
        return new, FLOOR_KEY
    if old is not None:
        return old, OLD_FLOOR_KEY
    return None, (OLD_FLOOR_KEY if OLD_FLOOR_KEY in prefs and FLOOR_KEY not in prefs
                  else FLOOR_KEY)


@dataclass(frozen=True)
class CompFloor:
    """A parsed pay floor (`compensation_floor`) -- a THRESHOLD, not a preference.

    It answers "below what number would I not take the job". Whether you
    *prefer* more money is a different question, is true of everyone, and is
    therefore not a setting -- see decision 7 in the ADR. `no_floor` means "no
    threshold"; it does not mean pay is ignored.

    See docs/decisions/0001-compensation-and-work-authorization.md.

    Three states are deliberately distinguishable, because "I have not decided"
    and "I have no floor" are different answers and conflating them is how the
    field sat null for months while reading as no constraint:

        None (not this class)  undecided  -- discovery proceeds, tailoring refuses
        CompFloor(no_floor)    none wanted
        CompFloor(default=..)  a floor, optionally per region

    Scoring applies it as a hard reject on a posting's TOP figure, and never
    to a posting whose pay is unknown. See ADR 0006 decision 5.
    """

    no_floor: bool = False
    default: int | None = None
    by_region: dict[str, int] = field(default_factory=dict)

    def for_region(self, region: str | None = None) -> int | None:
        """The floor that applies in `region`, or None when there is none."""
        if self.no_floor:
            return None
        if region and region in self.by_region:
            return self.by_region[region]
        return self.default


def parse_compensation_floor(value: Any, regions: dict[str, Any],
                             key: str = FLOOR_KEY) -> CompFloor | None:
    """Validate the SHAPE of the floor. None means undecided. `key` is the
    name it was found under, so a message names what the profile says.

    Raises ConfigError on anything malformed rather than guessing, because a
    floor that silently reads as zero is indistinguishable from no floor.
    """
    if value is None:
        return None
    # bool before int: isinstance(True, int) is True in Python, and `true` is
    # not a compensation floor.
    if isinstance(value, bool):
        raise ConfigError(
            f"master_profile.yaml: {key} is true/false. Use "
            f"{NO_FLOOR!r} if you have no floor, or a number."
        )
    if isinstance(value, str):
        if value.strip() == NO_FLOOR:
            return CompFloor(no_floor=True)
        raise ConfigError(
            f"master_profile.yaml: {key} is {value!r}. The only "
            f"string accepted is {NO_FLOOR!r}; otherwise use a number or a map "
            "keyed to job_search_preferences.regions."
        )
    if isinstance(value, int):
        if value < 0:
            raise ConfigError(
                f"master_profile.yaml: {key} is {value}, which "
                "is negative."
            )
        return CompFloor(default=value)
    if isinstance(value, dict):
        known = set(regions) | {"default"}
        floors: dict[str, int] = {}
        default: int | None = None
        for region, amount in value.items():
            if region not in known:
                raise ConfigError(
                    f"master_profile.yaml: {key} has key "
                    f"{region!r}, which is not 'default' and not one of your "
                    f"regions ({', '.join(sorted(regions)) or 'none defined'})."
                )
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ConfigError(
                    f"master_profile.yaml: {key}[{region!r}] is "
                    f"{amount!r}, which is not a non-negative number."
                )
            if region == "default":
                default = amount
            else:
                floors[region] = amount
        if default is None and not floors:
            raise ConfigError(
                f"master_profile.yaml: {key} is an empty map. "
                f"Give a 'default', a region, or use {NO_FLOOR!r}."
            )
        return CompFloor(default=default, by_region=floors)
    raise ConfigError(
        f"master_profile.yaml: {key} is a "
        f"{type(value).__name__}, which is not a number, a map, or {NO_FLOOR!r}."
    )


def parse_tristate(value: Any, name: str) -> bool | None:
    """A yes/no answer that may legitimately not have been given yet."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ConfigError(
        f"master_profile.yaml: job_search_preferences.{name} is {value!r}. "
        "Use true, false, or leave it null until you have decided."
    )


# Far enough to include the next city over, close enough that "near me" still
# means something. Overridden per profile with radius_miles.
DEFAULT_RADIUS_MILES = 40.0


def parse_radius(value: Any) -> float:
    if value is None or value == "":
        return DEFAULT_RADIUS_MILES
    try:
        miles = float(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"master_profile.yaml: job_search_preferences.radius_miles is "
            f"{value!r}. Give a number of miles, e.g. 40.") from None
    if not 1 <= miles <= 3000:
        raise ConfigError(
            f"radius_miles is {miles:g}. Use 1 to 3000 miles -- for the whole "
            f"country, there is no radius to set: leave locations remote-only.")
    return miles


YEARS_FILTERS = ("reject", "rank", "off")


def parse_years_filter(value: Any) -> str:
    """reject | rank | off. Unset means reject, which is what it always did."""
    if value is None or value == "":
        return "reject"
    text = str(value).strip().lower()
    if text not in YEARS_FILTERS:
        raise ConfigError(
            f"master_profile.yaml: job_search_preferences.years_filter is "
            f"{value!r}. Use one of: reject (drop postings asking for more years "
            f"than max_years_experience), rank (keep them, ranked lower), or off "
            f"(ignore years).")
    return text


def parse_country(value: Any) -> str:
    """The profile's home country, as a pack code. Unset means the US, which
    is what every profile meant before countries existed."""
    from . import countries

    if value is None or str(value).strip() == "":
        return countries.DEFAULT
    code = str(value).strip().upper()
    if code not in countries.PACKS:
        raise ConfigError(
            f"master_profile.yaml: job_search_preferences.country is {value!r}. "
            f"Use one of: {', '.join(countries.codes())} (a two-letter country code).")
    return code


@dataclass
class Preferences:
    """The subset of the master profile that drives discovery."""

    target_titles: list[str] = field(default_factory=list)
    seniority: list[str] = field(default_factory=list)
    work_arrangement: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    have_keywords: list[str] = field(default_factory=list)
    # Tier-2 titles (sales track) and the multiplier applied to their score,
    # so an engineering role always outranks an equivalent sales one.
    fallback_titles: list[str] = field(default_factory=list)
    fallback_weight: float = 0.7
    # Years of experience this candidate can credibly claim, and what a posting
    # asking for more does: "reject" drops it, "rank" keeps it and ranks it
    # lower, "off" ignores years altogether. Plenty of postings ask for more
    # than they will actually hold out for; the choice to try is the operator's.
    max_years_experience: int = 3
    years_filter: str = "reject"
    # Where you are, and how far you would go. `home_location` is a ZIP or a
    # "City, ST"; left unset it is the first real place in `locations`, so an
    # existing profile gains distance scoring without being edited.
    home_location: str = ""
    radius_miles: float = DEFAULT_RADIUS_MILES
    # Named commute regions -> city substrings, for `matches --near <name>`.
    regions: dict[str, list[str]] = field(default_factory=dict)
    # Compensation and authorization. Stored and validated; NOT used by scoring.
    # docs/decisions/0001-compensation-and-work-authorization.md explains why.
    compensation_floor: "CompFloor | None" = None
    work_authorization: str | None = None
    needs_visa_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None
    # The one home country this profile searches in (a countries pack code).
    country: str = "US"

    def home(self):
        """The operator's origin as a Place, or None if it cannot be placed.

        Falls back to the first entry in `locations` that names a real town,
        so a profile written before this existed still gets distances.
        """
        from . import places

        for candidate in [self.home_location, *self.locations]:
            if not candidate or "remote" in str(candidate).lower():
                continue
            found = places.origin(str(candidate))
            if found is not None:
                return found
        return None

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "Preferences":
        prefs = profile.get("job_search_preferences") or {}
        missing = [k for k in ("target_titles", "locations") if not prefs.get(k)]
        if missing:
            raise ConfigError(
                f"master_profile.yaml: job_search_preferences.{'/'.join(missing)} "
                "is empty. Fill it in before running discovery — guessing here "
                "would silently skew every result."
            )
        floor, floor_key = floor_setting(prefs)
        return cls(
            target_titles=list(prefs.get("target_titles") or []),
            seniority=list(prefs.get("seniority") or []),
            work_arrangement=list(prefs.get("work_arrangement") or []),
            locations=list(prefs.get("locations") or []),
            exclude_keywords=list(prefs.get("exclude_keywords") or []),
            have_keywords=list(
                ((profile.get("ats_keywords") or {}).get("have")) or []
            ),
            fallback_titles=list(prefs.get("fallback_titles") or []),
            fallback_weight=float(prefs.get("fallback_weight") or 0.7),
            max_years_experience=int(prefs.get("max_years_experience") or 3),
            years_filter=parse_years_filter(prefs.get("years_filter")),
            home_location=str(prefs.get("home_location") or "").strip(),
            radius_miles=parse_radius(prefs.get("radius_miles")),
            regions={k: list(v) for k, v in (prefs.get("regions") or {}).items()},
            compensation_floor=parse_compensation_floor(
                floor, prefs.get("regions") or {}, floor_key),
            work_authorization=(prefs.get("work_authorization") or None),
            needs_visa_sponsorship=parse_tristate(
                prefs.get("needs_visa_sponsorship"), "needs_visa_sponsorship"
            ),
            willing_to_relocate=parse_tristate(
                prefs.get("willing_to_relocate"), "willing_to_relocate"
            ),
            country=parse_country(prefs.get("country")),
        )


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"master profile not found at {path}")
    with path.open(encoding="utf-8") as fh:
        profile = yaml.safe_load(fh)
    if path == PROFILE_PATH and isinstance(profile, dict):
        # The live profile sets the country the app works in (one per
        # profile); a draft being read or previewed does not.
        from . import countries
        prefs = profile.get("job_search_preferences") or {}
        countries.activate(parse_country(prefs.get("country") if isinstance(prefs, dict)
                                         else None))
    return profile


COACH_PATH = RESOURCES / "coach.yaml"


def load_coach(path: Path | None = None) -> dict[str, Any]:
    """Word lists and thresholds for labels and the resume report (ADR 0023).
    A missing file means the built-in defaults, never a crash."""
    path = path or COACH_PATH
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def seed_companies(path: Path | None = None) -> Path:
    """An installed copy's own companies list, copied from the shipped seed
    the first time it is needed. Never overwrites."""
    path = Path(path or COMPANIES_PATH)
    if not path.exists() and path != SEED_COMPANIES and SEED_COMPANIES.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SEED_COMPANIES.read_bytes())
    return path


def load_sources(path: Path = COMPANIES_PATH) -> list[dict[str, Any]]:
    if path == COMPANIES_PATH:
        seed_companies(path)
    if not path.exists():
        raise ConfigError(f"companies config not found at {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return list(data.get("sources") or [])
