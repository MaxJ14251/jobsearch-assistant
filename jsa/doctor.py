"""What will not work yet, in plain language. Changes nothing.

A newcomer copies master_profile.example.yaml and is then alone with a few
hundred lines of YAML. The failures this project has actually produced are all
silent until late:

    work_authorization left null      tailoring refuses, at the last step
    target_titles left as examples    every job scores 0
    a bullet with no tags             never selected, for any role
    a tag no posting uses             dead weight, invisible
    a credential line left out        the degree claim stops being explicit

`jsa doctor` says all of that up front. It reads; it never writes. It exits 1
when something must be fixed before the tool works, and 0 when the rest is
merely worth knowing -- so it can be a first check for someone setting up.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import yaml

from .config import ROOT

# What counts as "still the example". Read from the example file rather than
# copied here: a copy is personal-looking data in source (the scanner blocked
# exactly that), and it would drift the first time the example changed.
# Deliberately not "example": example.com and example.test are what a real
# person's test address looks like, and the example file's own values are read
# in below anyway.
_GENERIC = re.compile(r"\byour\b|\bnearby\b|would relocate", re.I)


def example_values() -> set[str]:
    """Every identity and location string the shipped example contains."""
    path = ROOT / "profile" / "master_profile.example.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return set()
    values: set[str] = set()
    ident = data.get("identity") or {}
    for value in list(ident.values()) + list((ident.get("location") or {}).values()):
        if isinstance(value, str) and value.strip():
            values.add(value.strip().lower())
    for value in (data.get("links") or {}).values():
        if isinstance(value, str) and value.strip():
            values.add(value.strip().lower())
    prefs = data.get("job_search_preferences") or {}
    for place in prefs.get("locations") or []:
        values.add(str(place).strip().lower())
    for towns in (prefs.get("regions") or {}).values():
        for town in towns or []:
            values.add(str(town).strip().lower())
    return values


MIN_POSTINGS_FOR_TAGS = 20


@dataclass
class Finding:
    """One thing to fix or know. `blocking` decides the exit code."""

    blocking: bool
    what: str
    fix: str
    where: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def add(self, blocking: bool, what: str, fix: str, where: str = "") -> None:
        self.findings.append(Finding(blocking, what, fix, where))

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def advisory(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocking]

    @property
    def ok(self) -> bool:
        return not self.blocking


# Values the example ships that a reader is meant to KEEP, not overwrite. The
# example's locations list mixes the two: "Your City, ST" is a stand-in,
# "Remote (US)" is a real entry doing a real job. Treating every example
# location as a stand-in made doctor tell every reader to "replace or delete"
# the one line that scores remote roles 1.0 instead of 0.7 -- and for a reader
# outside a big metro, remote roles are the whole product.
_KEEP = re.compile(r"^\s*remote\b", re.I)


def _is_placeholder(value: Any) -> bool:
    """Still the example's text, or obviously a stand-in for real text."""
    if not isinstance(value, str) or not value.strip():
        return False
    if _KEEP.match(value):
        return False
    return (value.strip().lower() in example_values()
            or bool(_GENERIC.search(value)))


def check_identity(profile: dict[str, Any], report: Report) -> None:
    ident = profile.get("identity") or {}
    if not (ident.get("full_name") or "").strip() or _is_placeholder(ident.get("full_name")):
        report.add(True, "Your name is still the example's",
                   "Set identity.full_name. It is printed on every document.",
                   "profile/master_profile.yaml")
    for field_name in ("email", "phone"):
        value = ident.get(field_name)
        if not (value or "").strip() or _is_placeholder(value):
            report.add(True, f"Your {field_name} is missing or still an example",
                       f"Set identity.{field_name}: employers reply to it.",
                       "profile/master_profile.yaml")
    report.checked.append("name and contact details")


def check_preferences(profile: dict[str, Any], report: Report) -> None:
    prefs = profile.get("job_search_preferences") or {}

    if not (prefs.get("work_authorization") or "").strip():
        report.add(True, "work_authorization is unanswered",
                   "Write how you can work in the US, e.g. 'US citizen'. "
                   "Drafting a document refuses until this is answered.",
                   "profile/master_profile.yaml")
    if prefs.get("compensation_floor_usd") is None:
        report.add(True, "compensation_floor_usd is undecided",
                   "Use no_floor if you have no minimum, or a number. "
                   "Undecided is not the same as no minimum, so drafting "
                   "refuses rather than guessing.",
                   "profile/master_profile.yaml")

    titles = [t for t in (prefs.get("target_titles") or []) if str(t).strip()]
    if not titles:
        report.add(True, "No target job titles",
                   "List the titles you want. Discovery scores every posting "
                   "against them; with none, everything scores 0.",
                   "profile/master_profile.yaml")

    places = [p for p in (prefs.get("locations") or []) if str(p).strip()]
    unedited = [p for p in places if _is_placeholder(p)]
    if not places:
        report.add(True, "No locations",
                   "List where you would work, and 'Remote (US)' if remote "
                   "suits you.", "profile/master_profile.yaml")
    elif len(unedited) == len(places):
        report.add(True, "Your locations are still the example's placeholders",
                   "Replace them with real cities, written 'City, ST'.",
                   "profile/master_profile.yaml")
    elif unedited:
        report.add(False, f"{len(unedited)} location(s) are still placeholders",
                   "Replace or delete them: " + ", ".join(unedited))

    for name, cities in (prefs.get("regions") or {}).items():
        if any(_is_placeholder(c) for c in cities or []):
            report.add(False, f"Region {name!r} still holds example towns",
                       "Rename the region and list real towns, or delete it. "
                       "Regions are only used by `matches --near`.")
    report.checked.append("search preferences")


def check_evidence(profile: dict[str, Any], report: Report) -> None:
    """Bullets, summaries and tags: the material every document is built from."""
    from .tailor import collect_bullets

    bullets = collect_bullets(profile)
    if not bullets:
        report.add(True, "No experience or project bullets",
                   "Add what you have done under experience: or projects:. "
                   "Every document is built from these and nothing else.",
                   "profile/master_profile.yaml")
    if not (profile.get("summaries") or []):
        report.add(True, "No summary variants",
                   "Add at least one summary. It opens every document.",
                   "profile/master_profile.yaml")

    untagged = sorted(b.id for b in bullets.values() if not b.tags)
    if untagged:
        report.add(False, f"{len(untagged)} bullet(s) have no tags",
                   "Tags are how a bullet gets picked for a role. Untagged "
                   "ones are only chosen when little else fits: "
                   + ", ".join(untagged[:6]))

    families = {b.family for b in bullets.values() if b.family}
    if bullets and not families:
        report.add(False, "No bullet names a family",
                   "Set family: on each experience and project entry "
                   "(e.g. ai_engineering, sales). Selection favours the "
                   "family that fits the role.")
    report.checked.append("bullets, summaries and tags")


def check_credentials(profile: dict[str, Any], report: Report) -> None:
    """A degree claim must be explicit, in your words, or absent."""
    for entry in profile.get("education") or []:
        if not (entry.get("credential") or "").strip():
            report.add(
                True,
                f"{entry.get('institution', 'An education entry')} has no "
                "credential line",
                "Write exactly what you hold, e.g. 'BS Computer Science, 2020' "
                "or 'Undergraduate coursework completed (degree not "
                "conferred)'. Documents print this verbatim, so nothing is "
                "left for a model to imply.",
                "profile/master_profile.yaml")
    for entry in profile.get("certifications") or []:
        if not (entry.get("name") or "").strip():
            report.add(False, "A certification has no name",
                       "Name it or remove it.")
    report.checked.append("education and certifications")


def check_resume_fields(profile: dict[str, Any], report: Report) -> None:
    """A hint, never a blocker: the optional resume fields this profile
    doesn't use yet (plan 11). One line, so it can't drown a real finding."""
    exp = profile.get("experience") or []
    unused = [name for name, used in (
        ("experience[].company_descriptor", any(e.get("company_descriptor") for e in exp)),
        ("projects[].start / end", any(p.get("start") for p in profile.get("projects") or [])),
        ("certifications[].display_name",
         any(c.get("display_name") for c in profile.get("certifications") or [])),
        ("skill_labels", bool(profile.get("skill_labels"))),
        ("summaries[].role_kinds / keywords",
         any(s.get("role_kinds") or s.get("keywords") for s in profile.get("summaries") or [])),
        ("resume.date_style", "date_style" in (profile.get("resume") or {})),
        ("resume.skills_include_project_stack",
         "skills_include_project_stack" in (profile.get("resume") or {})),
        ("coach.disabled / gap_months", bool(profile.get("coach"))),
    ) if not used]
    if unused:
        report.add(False, f"{len(unused)} optional resume field(s) unused",
                   "Optional; the defaults work. See the comments in "
                   "profile/master_profile.example.yaml: " + ", ".join(unused))
    report.checked.append("optional resume fields")


def check_tracker(con: sqlite3.Connection | None, profile: dict[str, Any],
                  report: Report) -> None:
    if con is None:
        report.add(True, "No tracker database yet",
                   "Run `jsa init`, then `jsa discover`.")
        report.checked.append("the tracker")
        return

    jobs = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    if jobs == 0:
        report.add(False, "The tracker holds no postings yet",
                   "Run `jsa discover`. It needs no API key.")
    report.checked.append(f"the tracker ({jobs} posting(s))")

    from . import backup
    age = backup.newest_age_days()
    if age is None:
        report.add(False, "The tracker has never been backed up",
                   "Run `jsa backup`: it is the only record of your applications.")
    elif age > backup.BACKUP_STALE_DAYS:
        report.add(False, f"The newest backup is {age:.0f} days old",
                   "Run `jsa backup`.")

    # `jsa daily` (plan 18): only once one has ever run.
    import json as _json
    from datetime import datetime, timezone

    from . import daily, db
    try:
        run = daily.latest(con)
    except sqlite3.OperationalError:
        run = None
    if run is not None and run["finished_at"] and not run["ok"]:
        failed = [s["name"] for s in _json.loads(run["steps_json"] or "[]")
                  if s.get("state") == "failed"]
        report.add(False, f"The last daily run failed: {', '.join(failed) or 'a step'}",
                   "See logs/ beside the tracker, then run `jsa daily` again.")
    elif run is not None:
        started = db.local_date(run["started_at"])
        # Both local: started is the run's local date (review R-30).
        if started and (datetime.now().date() - started).days > daily.STALE_DAYS:
            report.add(False, f"The last daily run was on {started}",
                       "Run `jsa daily`, or check the task you scheduled for it.")

    if jobs >= MIN_POSTINGS_FOR_TAGS:
        from .tailor import is_dead, tag_weights, vocabulary

        weights = tag_weights(con, vocabulary(profile))
        dead = sorted(t for t in vocabulary(profile) if is_dead(t, weights))
        if dead:
            report.add(False, f"{len(dead)} tag(s) match no posting you have",
                       "They cannot help a bullet get picked. Reword them to "
                       "words postings use, or leave them: " + ", ".join(dead))
        report.checked.append("tag vocabulary against real postings")


MIN_POSTINGS_FOR_MARKET = 20

# Below this many non-remote postings in your own state, the honest answer is
# "this is a remote-roles tool for you". Measured over 9,451 postings from all
# 50 shipped feeds: Los Angeles 250 in-state, Seattle 168, Columbus 5, Boise 0.
# The first two are big metros where large multi-site employers already post;
# the last two are most of the country.
THIN_MARKET = 25


def check_market(profile: dict[str, Any], con: sqlite3.Connection | None,
                 report: Report) -> None:
    """What the shipped feeds actually hold for *your* part of the country.

    The feed list is a few dozen large tech employers plus the AI labs. That
    produces remote roles for everybody and local roles wherever those
    employers happen to have offices — which is a real answer, but only if
    the tool says so. Otherwise a reader in Ohio spends a week concluding the
    software is broken, when what is missing is employers near them.
    """
    if con is None:
        return
    from .scoring import in_state, is_non_us, preferred_states

    prefs = profile.get("job_search_preferences") or {}
    states = preferred_states(prefs.get("locations") or [])
    rows = con.execute(
        "SELECT location, remote FROM jobs WHERE archived_at IS NULL").fetchall()
    if len(rows) < MIN_POSTINGS_FOR_MARKET:
        return                      # check_tracker already said it is empty

    # What the radius actually holds, now that postings sit on a map.
    from . import places
    from .config import Preferences

    try:
        home = Preferences.from_profile(profile).home()
        radius = Preferences.from_profile(profile).radius_miles
    except Exception:  # noqa: BLE001 - doctor reports, it never raises
        home, radius = None, 0.0
    if home is not None:
        inside = unplaceable = 0
        for r in rows:
            if (r["remote"] or "") == "remote":
                continue
            parsed = places.parse(r["location"] or "")
            if not parsed.places:
                unplaceable += 1
                continue
            if places.nearest(home, parsed) <= radius:
                inside += 1
        report.checked.append(
            f"postings within {radius:.0f} miles of {home} ({inside})")
        if unplaceable:
            report.add(
                False,
                f"{unplaceable} posting(s) name a place this cannot find",
                "They are scored on everything else and never dropped. Mostly "
                "postings that name no town, a department instead of a place, "
                "or a town too new for the shipped Census data.")

    remote = sum(1 for r in rows if (r["remote"] or "") == "remote")
    local = sum(1 for r in rows
                if (r["remote"] or "") != "remote"
                and not is_non_us(r["location"] or "")
                and any(in_state(r["location"] or "", s) for s in states))
    report.checked.append(f"what your tracker holds near you "
                          f"({local} local, {remote} remote)")

    if not states:
        report.add(False, "Your locations name no state",
                   "Write them 'City, ST'. Without a state, a posting in "
                   "your state but not your city cannot be recognised.")
        return
    named = ", ".join(sorted(s.upper() for s in states))
    if local < THIN_MARKET:
        import os

        if os.environ.get("MUSE_API_KEY", "").strip():
            fix = (f"{remote} remote role(s) are in your tracker. The "
                   f"nationwide source is configured, so run `jsa discover` "
                   f"again -- it asks about your own cities. If it stays thin "
                   f"after that, the local market for these titles is thin, "
                   f"and adding employers near you to config/companies.yaml "
                   f"is the next lever.")
        else:
            fix = (f"{remote} remote role(s) are in your tracker, and local "
                   f"coverage is this thin because every shipped feed is one "
                   f"large employer's own board. Set MUSE_API_KEY in .env "
                   f"(free, from themuse.com/developers/api/v2/apps): that "
                   f"source asks about YOUR cities across many employers. "
                   f"Measured 2026-09-26, it returned 9 Boise postings per "
                   f"run where these feeds had none at all. You can also add "
                   f"employers near you to config/companies.yaml.")
        report.add(False, f"Only {local} on-site posting(s) in {named}", fix)


def check_api_key(report: Report) -> None:
    """Whether a key is configured. Never what it is."""
    from . import llm

    try:
        llm.api_key()
    except Exception:  # noqa: BLE001 - absence is a finding, not a crash
        report.add(False, "No API key configured",
                   "Discovery, matches and the dashboard work without one. "
                   "Drafting documents and interview prep need it: copy "
                   ".env.example to .env and add your key.")
    report.checked.append("API key (presence only)")


def check_basemap(report: Report) -> None:
    """The ground under the live map (n23). Advisory: without it the map
    still works and still agrees with its filter, it is just flat."""
    from . import basemap

    if not basemap.available():
        report.add(False, "The map has no land, water or roads under it",
                   "The basemap files are missing from data/. Rebuild them "
                   "with `python tools/build_map_data.py --only basemap` "
                   "(downloads public Census and Natural Earth files once), "
                   "or restore data/us_basemap_*.json.gz from the repository.")
    report.checked.append("map data")


def check_secrets(report: Report) -> None:
    """That .env cannot be committed, and the pre-commit scanner is on.

    Asked when the mailbox setting arrived (ADR 0021): an app password sits in
    .env, so whether that file can reach GitHub is worth checking, not
    assuming. Says whether the mailbox is set up; never what it is set to.
    """
    import subprocess

    from .config import ROOT
    from . import inbox

    def git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None

    ignored = git("check-ignore", "-q", ".env")
    if ignored is not None and ignored.returncode == 1:
        report.add(True, ".env is not ignored by git",
                   "It holds your keys. Add `.env` to .gitignore before you commit.")
    hooks = git("config", "core.hooksPath")
    if (hooks is not None and hooks.returncode in (0, 1)
            and (hooks.stdout or "").strip() != ".githooks"):
        report.add(False, "The pre-commit scanner is not switched on",
                   "Run `git config core.hooksPath .githooks` so every commit is "
                   "checked for keys and personal details.")
    mailbox = "set up" if inbox.settings() is not None else "not set up (optional)"
    report.checked.append(f".env ignored, commit hook, mailbox {mailbox}")


def run(profile: dict[str, Any] | None,
        con: sqlite3.Connection | None) -> Report:
    """Every check. Reads only."""
    report = Report()
    if profile is None:
        report.add(True, "No profile file",
                   "Copy profile/master_profile.example.yaml to "
                   "profile/master_profile.yaml and fill it in.")
        check_tracker(con, {}, report)
        check_api_key(report)
        return report

    check_identity(profile, report)
    check_preferences(profile, report)
    check_evidence(profile, report)
    check_credentials(profile, report)
    check_resume_fields(profile, report)
    check_tracker(con, profile, report)
    check_market(profile, con, report)
    check_basemap(report)
    check_api_key(report)
    check_secrets(report)
    return report
