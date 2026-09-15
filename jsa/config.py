"""Paths and configuration loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


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


PROFILE_PATH = ROOT / "profile" / "master_profile.yaml"
COMPANIES_PATH = ROOT / "config" / "companies.yaml"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
DB_PATH = Path(os.environ.get("JSA_DB", ROOT / "jobsearch.db"))
OUTPUT_DIR = ROOT / "output"

PROJECT_URL = "https://github.com/MaxJ14251/jobsearch-assistant"


def user_agent() -> str:
    """User-Agent sent to job boards.

    Job boards reasonably want a contact address on automated traffic, but the
    contact must be whoever is *running* the tool — never a value baked into
    the source. Resolution order: JSA_CONTACT_EMAIL, then the profile's
    identity.email, then no contact at all.
    """
    contact = os.environ.get("JSA_CONTACT_EMAIL", "").strip()
    if not contact:
        try:
            profile = load_profile()
            contact = ((profile.get("identity") or {}).get("email") or "").strip()
        except Exception:  # noqa: BLE001 — a missing profile must not break a fetch
            contact = ""
    suffix = f"; contact {contact}" if contact else ""
    return f"jobsearch-assistant/0.1 (+{PROJECT_URL}{suffix})"


REQUEST_TIMEOUT = 20.0
POLITE_DELAY_S = 1.0  # between requests to the same host


class ConfigError(RuntimeError):
    """Raised when configuration is missing or contains an unresolved TODO."""


NO_FLOOR = "no_floor"


@dataclass(frozen=True)
class CompFloor:
    """A parsed compensation_floor_usd -- a THRESHOLD, not a preference.

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

    Nothing in scoring consumes this yet, by decision: every jobs.salary_min is
    null because nothing extracts salary. A floor wired in today would be a
    no-op that looked like a feature.
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


def parse_compensation_floor(value: Any, regions: dict[str, Any]) -> CompFloor | None:
    """Validate the SHAPE of compensation_floor_usd. None means undecided.

    Raises ConfigError on anything malformed rather than guessing, because a
    floor that silently reads as zero is indistinguishable from no floor.
    """
    if value is None:
        return None
    # bool before int: isinstance(True, int) is True in Python, and `true` is
    # not a compensation floor.
    if isinstance(value, bool):
        raise ConfigError(
            "master_profile.yaml: compensation_floor_usd is true/false. Use "
            f"{NO_FLOOR!r} if you have no floor, or a number."
        )
    if isinstance(value, str):
        if value.strip() == NO_FLOOR:
            return CompFloor(no_floor=True)
        raise ConfigError(
            f"master_profile.yaml: compensation_floor_usd is {value!r}. The only "
            f"string accepted is {NO_FLOOR!r}; otherwise use a number or a map "
            "keyed to job_search_preferences.regions."
        )
    if isinstance(value, int):
        if value < 0:
            raise ConfigError(
                f"master_profile.yaml: compensation_floor_usd is {value}, which "
                "is negative."
            )
        return CompFloor(default=value)
    if isinstance(value, dict):
        known = set(regions) | {"default"}
        floors: dict[str, int] = {}
        default: int | None = None
        for key, amount in value.items():
            if key not in known:
                raise ConfigError(
                    f"master_profile.yaml: compensation_floor_usd has key "
                    f"{key!r}, which is not 'default' and not one of your "
                    f"regions ({', '.join(sorted(regions)) or 'none defined'})."
                )
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                raise ConfigError(
                    f"master_profile.yaml: compensation_floor_usd[{key!r}] is "
                    f"{amount!r}, which is not a non-negative number."
                )
            if key == "default":
                default = amount
            else:
                floors[key] = amount
        if default is None and not floors:
            raise ConfigError(
                "master_profile.yaml: compensation_floor_usd is an empty map. "
                f"Give a 'default', a region, or use {NO_FLOOR!r}."
            )
        return CompFloor(default=default, by_region=floors)
    raise ConfigError(
        f"master_profile.yaml: compensation_floor_usd is a "
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
    # Years of experience this candidate can credibly claim. A posting asking
    # for materially more is a hard reject.
    max_years_experience: int = 3
    # Named commute regions -> city substrings, for `matches --near <name>`.
    regions: dict[str, list[str]] = field(default_factory=dict)
    # Compensation and authorization. Stored and validated; NOT used by scoring.
    # docs/decisions/0001-compensation-and-work-authorization.md explains why.
    compensation_floor: "CompFloor | None" = None
    work_authorization: str | None = None
    needs_visa_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None

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
            regions={k: list(v) for k, v in (prefs.get("regions") or {}).items()},
            compensation_floor=parse_compensation_floor(
                prefs.get("compensation_floor_usd"), prefs.get("regions") or {}
            ),
            work_authorization=(prefs.get("work_authorization") or None),
            needs_visa_sponsorship=parse_tristate(
                prefs.get("needs_visa_sponsorship"), "needs_visa_sponsorship"
            ),
            willing_to_relocate=parse_tristate(
                prefs.get("willing_to_relocate"), "willing_to_relocate"
            ),
        )


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"master profile not found at {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_sources(path: Path = COMPANIES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        raise ConfigError(f"companies config not found at {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return list(data.get("sources") or [])
