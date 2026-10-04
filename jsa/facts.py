"""Answers that are facts in the profile: no model, the person's own words.

Shared by interview prep (`prep.standard_drills`) and the application answer
bank (`answers.py`, plan 17). Nothing here invents: a value the profile
doesn't hold comes back as None, and the caller says it's undecided.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .coach import _label, _month


def _edu(profile: dict[str, Any]) -> dict[str, Any]:
    return (profile.get("education") or [{}])[0] or {}


def education_line(profile: dict[str, Any]) -> str | None:
    """Institution, field, years and the credential VERBATIM, as the resume
    prints it. None without an education entry."""
    edu = _edu(profile)
    if not edu.get("institution"):
        return None
    line = str(edu["institution"])
    if edu.get("field"):
        line += f" — {edu['field']}"
    if edu.get("start") or edu.get("end"):
        line += f", {edu.get('start') or ''}–{edu.get('end') or ''}".rstrip("–")
    credential = " ".join(str(edu.get("credential") or "").split())
    if credential:
        line += f" · {credential}"
    return line


# Words in a credential line that say the degree was NOT completed. Without
# one, the line is something the person holds ("BS Computer Science, 2020")
# and is stated as written; with one, it is stated as coursework.
_NOT_COMPLETED = re.compile(
    r"\b(not|no|never|without|incomplete|unfinished|didn'?t|coursework|"
    r"some college|in progress|pursuing|expected)\b", re.I)


def claims_a_degree(credential: str) -> bool:
    """Whether the person's own credential line states a degree they hold."""
    return bool(credential.strip()) and not _NOT_COMPLETED.search(credential)


def degree_answer(profile: dict[str, Any]) -> str:
    """Built from the credential line, never generated, and identical every
    time. A degree the person holds is stated as they wrote it; coursework is
    stated as coursework, in their words, with no word that implies more."""
    edu = _edu(profile)
    credential = " ".join(str(edu.get("credential") or "").split())
    institution = edu.get("institution") or "my university"
    field_of_study = edu.get("field")
    start, end = edu.get("start"), edu.get("end")
    years = f" from {start} to {end}" if start and end else ""
    if not credential:
        return ("Your profile has no credential line yet: write exactly what "
                "you hold under education[].credential before answering this.")
    if claims_a_degree(credential):
        return f"{credential}, {institution}{years}."
    studied = f"I studied {field_of_study} at {institution}{years}" if field_of_study \
        else f"I studied at {institution}{years}"
    since = []
    certs = profile.get("certifications") or []
    if certs:
        latest = max((str(c.get("issued") or "")[:4] for c in certs), default="")
        since.append(f"{len(certs)} certification(s)"
                     + (f", most recently in {latest}" if latest else ""))
    if profile.get("projects"):
        since.append("the applied projects on my resume")
    tail = (" Since then I've kept building in the field: " + " and ".join(since) + "."
            if since else "")
    return (f"{studied}. In my own words: {credential}.{tail} Where a posting "
            "asks for a degree or equivalent experience, the equivalent "
            "experience is what I'd point to.")


def _jobs(profile: dict[str, Any], today: dt.date) -> list[tuple[int, int, dict]]:
    now = today.year * 12 + today.month - 1
    out = []
    for e in profile.get("experience") or []:
        start = _month(e.get("start"))
        if start is None:
            continue
        end = now if e.get("current") else _month(e.get("end"), end=True)
        out.append((start, end if end is not None else start, e))
    return sorted(out, key=lambda t: (t[0], t[1]))


def gap_since(profile: dict[str, Any], today: dt.date | None = None) -> tuple[str, dict] | None:
    """(month the last job ended, that job) when there is no current job and
    the gap is longer than the resume report's threshold; else None."""
    from .coach import GAP_MONTHS

    today = today or dt.date.today()
    jobs = _jobs(profile, today)
    if not jobs or any(e.get("current") for _, _, e in jobs):
        return None
    now = today.year * 12 + today.month - 1
    _, end, last = max(jobs, key=lambda t: t[1])
    months = int((profile.get("coach") or {}).get("gap_months") or GAP_MONTHS)
    if now - end <= months:
        return None
    return _label(end), last


def gap_answer(profile: dict[str, Any], today: dt.date | None = None) -> str | None:
    """From the profile's own dates and what it records since. None with no gap."""
    today = today or dt.date.today()
    gap = gap_since(profile, today)
    if gap is None:
        return None
    when, last = gap
    ended = _month(last.get("end"), end=True) or 0
    after = []
    for c in profile.get("certifications") or []:
        issued = _month(c.get("issued"))
        if issued is not None and issued > ended:
            after.append(str(c.get("display_name") or c.get("name")))
    projects = [str(p.get("name")) for p in profile.get("projects") or [] if p.get("name")]
    role = " at ".join(str(x) for x in (last.get("title"), last.get("company")) if x)
    answer = f"I left my last role{f' ({role})' if role else ''} in {when}."
    if after:
        answer += " Since then I completed " + "; ".join(after[:3]) + "."
    if projects:
        answer += " I've also been building " + "; ".join(projects[:3]) + "."
    return answer


def years_of_experience(profile: dict[str, Any],
                        today: dt.date | None = None) -> tuple[int, str] | None:
    """(months, wording) summed over experience, overlaps counted once.
    A computation from the person's own dates, labelled as one."""
    today = today or dt.date.today()
    jobs = _jobs(profile, today)
    if not jobs:
        return None
    total, cur_start, cur_end = 0, jobs[0][0], jobs[0][1]
    for start, end, _ in jobs[1:]:
        if start <= cur_end + 1:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start + 1
            cur_start, cur_end = start, end
    total += cur_end - cur_start + 1
    years, months = divmod(total, 12)
    parts = ([f"{years} year{'s' if years != 1 else ''}"] if years else []) + \
            ([f"{months} month{'s' if months != 1 else ''}"] if months else [])
    return total, f"about {' '.join(parts) or 'under a month'} (from your profile dates)"
