"""Everything the app assumes about one country, kept in one place.

A profile has one home country (`job_search_preferences.country`, default
US). Each country is a pack: a module here holding its regions, its shipped
data files and, as the split goes on, its pay, place and form conventions.
The rest of the app asks the pack instead of assuming the US.

The US is the only pack so far, so a profile that names no country behaves
exactly as before. docs/decisions (the country-pack ADR) records the split.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import ModuleType
from typing import Iterator

from . import us

PACKS: dict[str, ModuleType] = {us.CODE: us}
DEFAULT = us.CODE


def get(code: str | None = None) -> ModuleType:
    """The pack for a country code ("US"); the default when none is given.
    An unknown code raises KeyError: callers turn it into a plain message."""
    return PACKS[(code or DEFAULT).strip().upper()]


def codes() -> list[str]:
    return sorted(PACKS)


def country_of_source(kind: str) -> str | None:
    """The country a source kind searches, or None for an employer's own
    board, which belongs to no country."""
    for code, pack in PACKS.items():
        if kind in pack.SOURCE_KINDS:
            return code
    return None


def source_is_elsewhere(kind: str) -> bool:
    """A source that searches some other country than the working one."""
    home = country_of_source(kind)
    return home is not None and home != active().CODE


# The pack the running app works in. One profile is served at a time, so the
# country is set where the profile is loaded (config.load_profile) and read
# at call time by everything country-specific.
_active = DEFAULT


def active() -> ModuleType:
    return PACKS[_active]


def activate(code: str | None) -> ModuleType:
    """Make `code` the working country (unset: the default). Unknown codes
    raise KeyError, as get() does."""
    global _active
    pack = get(code)
    _active = pack.CODE
    return pack


@contextmanager
def using(code: str) -> Iterator[ModuleType]:
    """Work in another country for a block, then put the previous one back."""
    before = _active
    try:
        yield activate(code)
    finally:
        activate(before)
