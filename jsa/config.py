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
