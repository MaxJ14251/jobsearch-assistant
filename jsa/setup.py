"""The newcomer setup page's work (plan 20): preferences into the DRAFT,
"Make this my profile" on first run only, and a first discovery in the
background.

The rules from ADR 0022 hold, with one dated exception: the dashboard writes
the draft profile, and it creates `master_profile.yaml` only when none exists
yet, or when the one there is still byte-identical to the shipped example.
It never overwrites a personal profile.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from . import config
from .resume_import import DRAFT_NAME, _q

PREFS = "job_search_preferences"


def draft_path() -> Path:
    return config.PROFILE_PATH.with_name(DRAFT_NAME)


def example_path() -> Path:
    return config.EXAMPLE_PROFILE


def start_from_example() -> Path:
    """Copy the example to the draft, unless a draft already exists."""
    path = draft_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(example_path(), path)
    return path


def load_draft() -> dict[str, Any] | None:
    path = draft_path()
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# --- the preferences block, replaced in place ----------------------------------------


def _emit(key: str, value: Any, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(value, dict):
        lines = [f"{pad}{key}:" + ("" if value else " {}")]
        for k, v in value.items():
            lines += _emit(str(k), v, indent + 2)
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{pad}{key}: []"]
        return [f"{pad}{key}:"] + [f"{pad}  - {_scalar(v)}" for v in value]
    return [f"{pad}{key}: {_scalar(value)}"]


def _scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return _q(value)
    if isinstance(value, (int, float)):
        return str(value)
    if value == "no_floor":
        return "no_floor"
    return _q(value)


def set_preferences(path: Path, prefs: dict[str, Any]) -> None:
    """Replace only the top-level `job_search_preferences:` block of the
    draft. Every other line, comments included, stays byte-identical. Keys
    the form doesn't cover (regions, seniority, ...) keep their values."""
    path = Path(path)
    if path.resolve() == config.PROFILE_PATH.resolve():
        raise ValueError("set_preferences writes the draft, never the live profile")
    # utf-8-sig: a byte-order mark hid the block, and a second one was
    # appended at the end (review R-29).
    raw = path.read_bytes().decode("utf-8-sig")
    lines = raw.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"{PREFS}:")), None)
    current = ((yaml.safe_load(raw) or {}).get(PREFS) or {})
    merged = {**current, **prefs}
    newline = "\r\n" if "\r\n" in raw else "\n"
    block = [ln + newline for ln in _emit(PREFS, merged, 0)]
    if start is None:
        lines += ([newline] if lines and lines[-1].strip() else []) + block
    else:
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#"):
            end += 1
        # Comments and blank lines just above the next key belong to it.
        while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
            end -= 1
        lines[start:end] = block
    path.write_bytes("".join(lines).encode("utf-8"))


def validate(form: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """The form's fields through the profile's own parsers. Returns
    (preferences, errors); nothing is written while errors is non-empty."""
    from . import places
    from .config import (ConfigError, parse_compensation_floor, parse_radius,
                         parse_tristate, parse_years_filter)

    def lines(name: str) -> list[str]:
        return [ln.strip() for ln in (form.get(name) or "").splitlines() if ln.strip()]

    errors: dict[str, str] = {}
    prefs: dict[str, Any] = {}
    prefs["target_titles"] = lines("target_titles")
    if not prefs["target_titles"]:
        errors["target_titles"] = "Name at least one job title you want."
    prefs["fallback_titles"] = lines("fallback_titles")
    locations = (["Remote (US)"] if form.get("remote") else []) + lines("locations")
    home = (form.get("home") or "").strip()
    if home:
        if places.origin(home) is None:
            errors["home"] = (f"{home!r} isn't a ZIP or a \"City, ST\" this can place. "
                              "Try a 5-digit ZIP.")
        else:
            prefs["home_location"] = home
            if "," in home and home not in locations:
                locations.append(home)
    prefs["locations"] = locations
    if not locations:
        errors["locations"] = "Tick remote, or give a home location or a city."
    radius = (form.get("radius") or "").strip()
    try:
        prefs["radius_miles"] = int(parse_radius(radius)) if radius else 40
    except ConfigError as exc:
        errors["radius"] = str(exc)
    prefs["work_authorization"] = (form.get("work_authorization") or "").strip() or None
    for name in ("needs_visa_sponsorship", "willing_to_relocate"):
        value = {"yes": True, "no": False}.get(form.get(name) or "")
        try:
            prefs[name] = parse_tristate(value, name)
        except ConfigError as exc:
            errors[name] = str(exc)
    floor = (form.get("floor") or "").strip().replace(",", "").replace("$", "")
    if floor:
        value: Any = "no_floor" if floor.lower() in ("no floor", "no_floor", "none") else floor
        if value != "no_floor":
            try:
                value = int(float(value))
            except ValueError:
                errors["floor"] = "Write \"no floor\" or a yearly figure, e.g. 95000."
        if "floor" not in errors:
            try:
                parse_compensation_floor(value, {})
                prefs["compensation_floor_usd"] = value
            except ConfigError as exc:
                errors["floor"] = str(exc)
    else:
        prefs["compensation_floor_usd"] = None
    years = (form.get("max_years") or "").strip()
    if years:
        try:
            prefs["max_years_experience"] = int(years)
        except ValueError:
            errors["max_years"] = "A whole number of years."
    try:
        prefs["years_filter"] = parse_years_filter(form.get("years_filter") or "reject")
    except ConfigError as exc:
        errors["years_filter"] = str(exc)
    prefs["exclude_keywords"] = lines("exclude_keywords")
    return prefs, errors


# --- first run: make the draft the profile -------------------------------------------


def can_adopt() -> bool:
    """Only when there is no profile yet, or it is still the shipped example."""
    live = config.PROFILE_PATH
    if not live.exists():
        return True
    return live.read_bytes() == example_path().read_bytes()


_ADOPT = threading.Lock()


def adopt() -> Path | None:
    """Create master_profile.yaml from the draft. Returns the backup taken
    when the example was replaced. Refuses a personal profile.

    The check is made again right before the write: the backup in between
    takes seconds, and a profile saved meanwhile was overwritten (review
    R-17). A new profile is created exclusively, so one that appears first
    is never replaced."""
    with _ADOPT:
        if not can_adopt():
            raise PermissionError("you already have a profile; it is never overwritten here")
        source = draft_path()
        if not source.exists():
            raise FileNotFoundError("there is no draft yet")
        live = config.PROFILE_PATH
        live.parent.mkdir(parents=True, exist_ok=True)
        copy = None
        if live.exists():                     # still the example: keep a copy first
            from . import backup
            copy = backup.make(label="manual") if config.DB_PATH.exists() else None
        data = source.read_bytes()
        if not live.exists():
            try:
                with open(live, "xb") as fh:
                    fh.write(data)
            except FileExistsError:
                raise PermissionError("a profile appeared while this ran; it is "
                                      "never overwritten here") from None
            return copy
        fd, tmp = tempfile.mkstemp(dir=live.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            if not can_adopt():
                raise PermissionError("your profile changed while this ran; it is "
                                      "never overwritten here")
            os.replace(tmp, live)             # atomic: never a half-written profile
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        return copy


# --- first discovery, in the background ------------------------------------------------


@dataclass
class Discovery:
    """One discovery at a time in a daemon thread (the Turbo worker pattern)."""
    started_at: str | None = None
    finished_at: str | None = None
    error: str = ""
    total: int = 0
    _thread: threading.Thread | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, run=None) -> bool:
        """False when one is already running."""
        from . import discover
        with self._lock:
            if self.running:
                return False
            entries = [e for e in config.load_sources()
                       if e.get("enabled") is not False and e.get("verified")]
            self.total = len(entries)
            self.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.finished_at, self.error = None, ""
            target = run or discover.discover

            def go():
                try:
                    target()
                except Exception as exc:  # noqa: BLE001 - shown on the page
                    self.error = (str(exc).splitlines() or [type(exc).__name__])[0][:300]
                finally:
                    self.finished_at = datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ")

            self._thread = threading.Thread(target=go, name="setup-discover", daemon=True)
            self._thread.start()
            return True

    def progress(self, con) -> dict[str, Any]:
        polled = 0
        if self.started_at:
            polled = con.execute("SELECT COUNT(*) FROM sources WHERE last_polled_at >= ?",
                                 (self.started_at,)).fetchone()[0]
        return {"running": self.running, "started": bool(self.started_at),
                "done": bool(self.finished_at) and not self.running,
                "polled": polled, "total": self.total, "error": self.error}
