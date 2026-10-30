"""Everything the app assumes about one country, kept in one place.

A profile has one home country (`job_search_preferences.country`, default
US). Each country is a pack: a module here holding its regions, its shipped
data files and, as the split goes on, its pay, place and form conventions.
The rest of the app asks the pack instead of assuming the US.

The US is the only pack so far, so a profile that names no country behaves
exactly as before. docs/decisions (the country-pack ADR) records the split.
"""

from __future__ import annotations

from types import ModuleType

from . import us

PACKS: dict[str, ModuleType] = {us.CODE: us}
DEFAULT = us.CODE


def get(code: str | None = None) -> ModuleType:
    """The pack for a country code ("US"); the default when none is given.
    An unknown code raises KeyError: callers turn it into a plain message."""
    return PACKS[(code or DEFAULT).strip().upper()]


def codes() -> list[str]:
    return sorted(PACKS)
