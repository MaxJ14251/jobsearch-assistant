"""Add one job by hand: from a link on a supported board, or from pasted text.

Discovery only knows the boards in config/companies.yaml. The operator finds
jobs everywhere else too, and until this module had no way to bring one in.

Two routes, and the line between them is deliberate:

- A link on Greenhouse, Lever, Ashby or Workday is read from that board's
  PUBLIC API, through the same adapter discovery uses, so the stored posting
  is exactly what discovery would have stored. The page the link points at is
  never fetched. The tool only ever requests an API URL it built itself from
  the parsed board and id, so a link can choose which posting, never which
  host.
- Anything else -- LinkedIn, Indeed, a company's own site -- is not fetched at
  all. The operator pastes the posting text. LinkedIn and Indeed forbid
  automated reading, and a scraper for arbitrary career pages would be wrong
  often enough to put invented facts into a posting.

Whatever the score, the job is stored: the operator chose it. A posting the
filters would have dropped is kept with a warning saying why.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import db, salary, sources
from .config import Preferences, load_sources
from .scoring import dedup_key, job_track, score_job

SUPPORTED = "Greenhouse, Lever, Ashby or Workday"

# A pasted posting shorter than this is almost certainly a title and a
# location, and would be drafted against with nothing to tailor to.
MIN_PASTED_CHARS = 200


class IntakeError(ValueError):
    """The job could not be added. The message says what to do instead."""


@dataclass(frozen=True)
class Link:
    kind: str                  # greenhouse | lever | ashby | workday
    board: str                 # the board token, or the Workday site
    job_id: str                # the posting's id on that board
    tenant: str | None = None  # Workday only
    wd: str | None = None      # Workday only
    path: str | None = None    # Workday only: /job/<location>/<slug>_<req>


@dataclass
class Added:
    job_id: int
    new: bool
    title: str
    company: str
    score: float
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    enriched: str = ""
    status: str | None = None   # the application's stage, if one exists


_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_WORKDAY_HOST = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$")


def parse_link(url: str) -> Link | None:
    """The board and posting a link names, or None for anything unsupported.

    Hosts are matched exactly. `boards.greenhouse.io.example.test` is not
    Greenhouse, and a link that only looks like one is never fetched.
    """
    try:
        parts = urlparse((url or "").strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        if len(segs) >= 3 and segs[1] == "jobs" and re.fullmatch(_TOKEN, segs[0]) \
                and segs[2].isdigit():
            return Link("greenhouse", segs[0], segs[2])
        query = parse_qs(parts.query)
        if segs[:2] == ["embed", "job_app"] and query.get("for") and query.get("token"):
            board, job = query["for"][0], query["token"][0]
            if re.fullmatch(_TOKEN, board) and job.isdigit():
                return Link("greenhouse", board, job)
        return None

    if host == "jobs.lever.co":
        if len(segs) >= 2 and re.fullmatch(_TOKEN, segs[0]) and re.fullmatch(_UUID, segs[1]):
            return Link("lever", segs[0], segs[1].lower())
        return None

    if host == "jobs.ashbyhq.com":
        if len(segs) >= 2 and re.fullmatch(_TOKEN, segs[0]) and re.fullmatch(_UUID, segs[1]):
            return Link("ashby", segs[0], segs[1].lower())
        return None

    match = _WORKDAY_HOST.match(host)
    if match:
        if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
            segs = segs[1:]                       # a locale: en-US
        if len(segs) >= 3 and segs[1] == "job" and re.fullmatch(_TOKEN, segs[0]):
            path = "/" + "/".join(segs[1:])
            req = segs[-1].rsplit("_", 1)[-1]
            return Link("workday", segs[0], req, tenant=match["tenant"],
                        wd=match["wd"], path=path)
        return None

    return None


def _entry_for(link: Link) -> dict[str, Any] | None:
    """The companies.yaml entry for this board, if the operator already has it."""
    try:
        entries = load_sources()
    except Exception:  # noqa: BLE001 - a missing config just means "no match"
        return None
    for entry in entries:
        if entry.get("kind") != link.kind:
            continue
        if link.kind == "workday":
            if (entry.get("tenant") or "").lower() == link.tenant \
                    and (entry.get("site") or "") == link.board:
                return entry
        elif (entry.get("board") or "").lower() == link.board.lower():
            return entry
    return None


def fetch_posting(link: Link) -> dict[str, Any]:
    """The posting, in the tracker's shape, read from the board's public API."""
    if link.kind == "workday":
        base = f"https://{link.tenant}.{link.wd}.myworkdayjobs.com/wday/cxs/{link.tenant}/{link.board}"
        try:
            with sources._client() as client:
                info = sources.workday_detail(client, base, link.path or "")
        except Exception as exc:  # noqa: BLE001
            raise IntakeError(f"Workday did not answer for that posting: {exc}") from exc
        if not info:
            raise IntakeError("Workday has no posting at that link. It may have "
                              "closed, or the link is to a search page.")
        return sources.workday_job(info, base, link.path or "")

    result = sources.fetch({"kind": link.kind, "board": link.board})
    if not result.ok:
        raise IntakeError(f"could not read the {link.kind.title()} board "
                          f"{link.board!r}: {result.status}")
    for job in result.jobs:
        if str(job.get("external_id")).lower() == link.job_id.lower():
            return job
    raise IntakeError(
        f"posting {link.job_id} is not on the {link.board!r} "
        f"{link.kind.title()} board any more. It has probably closed.")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "company"


def _store(con: sqlite3.Connection, job: dict[str, Any], *, company: str, slug: str,
           kind: str, source_url: str, prefs: Preferences,
           careers_url: str | None = None, priority: int = 3,
           named: bool = True) -> Added:
    """`named` is False when `company` is only a guess from the board token:
    an existing company keeps its name rather than being renamed to a guess."""
    existing_company = con.execute(
        "SELECT id, name FROM companies WHERE slug = ?", (slug,)).fetchone()
    if existing_company and not careers_url and not named:
        company_id, company = int(existing_company["id"]), existing_company["name"]
    elif existing_company and not careers_url:
        company_id = int(existing_company["id"])
        con.execute("UPDATE companies SET name = ?, updated_at = ? WHERE id = ?",
                    (company, db.utcnow(), company_id))
    else:
        company_id = db.upsert_company(con, name=company, slug=slug,
                                       careers_url=careers_url, priority=priority)
    # The same source name discovery uses, so a posting added here and later
    # found by discovery is one row, not two.
    source_id = db.upsert_source(con, name=f"{slug}-{kind}", kind=kind,
                                 url=source_url, company_id=company_id)

    job.update(salary.columns(salary.extract(job.get("description"))))
    score, reasons = score_job(job, prefs)
    warnings = []
    if score <= 0:
        warnings.append(
            "Your filters score this 0 (" + "; ".join(reasons[:2] or ["no reason given"])
            + "). Discovery would have dropped it. It is kept because you chose it.")

    before = con.execute(
        "SELECT id FROM jobs WHERE source_id = ? AND external_id = ?",
        (source_id, job["external_id"])).fetchone()
    job_id, is_new = db.upsert_job(con, {
        **job,
        "company_id": company_id,
        "source_id": source_id,
        "dedup_key": dedup_key(company_id, job["title"], job.get("location")),
        "track": job_track(job["title"], prefs),
        "match_score": score,
        "match_reasons": reasons,
    })
    if before is not None:
        is_new = False
    app = con.execute("SELECT status FROM applications WHERE job_id = ?",
                      (job_id,)).fetchone()
    return Added(job_id=job_id, new=is_new, title=job["title"], company=company,
                 score=score, reasons=list(reasons), warnings=warnings,
                 status=app["status"] if app else None)


def add_link(con: sqlite3.Connection, url: str, prefs: Preferences, *,
             company: str | None = None) -> Added:
    link = parse_link(url)
    if link is None:
        raise IntakeError(
            f"That link is not a {SUPPORTED} posting, so it will not be fetched. "
            "Paste the posting text instead (jsa add --paste, or the dashboard's "
            "'Paste a posting' form). LinkedIn and Indeed forbid automated "
            "reading; for a company's own site, look for an 'Apply' link that "
            "goes to one of those four.")
    posting = fetch_posting(link)
    entry = _entry_for(link)
    if entry is not None:
        return _store(con, posting, company=entry["company"], slug=entry["slug"],
                      kind=link.kind, source_url=sources.feed_url(entry) or url,
                      prefs=prefs, careers_url=entry.get("careers_url"),
                      priority=int(entry.get("priority", 3)))
    # The slug comes from the board, never the name, so naming it later
    # renames the company rather than creating a second one.
    name = company or link.board.replace("-", " ").replace("_", " ").title()
    added = _store(con, posting, company=name, slug=_slug(link.board),
                   kind=link.kind, source_url=url, prefs=prefs,
                   named=bool(company))
    if not company and added.new:
        added.warnings.append(
            f"Company recorded as {added.company!r}, from the board name. "
            "Pass --company to name it properly.")
    return added


def add_pasted(con: sqlite3.Connection, *, company: str, title: str, text: str,
               prefs: Preferences, url: str = "", location: str = "") -> Added:
    """A posting the operator copied by hand. Stored exactly as pasted."""
    company, title = (company or "").strip(), (title or "").strip()
    if not company or not title:
        raise IntakeError("A pasted posting needs the company and the job title.")
    if len((text or "").strip()) < MIN_PASTED_CHARS:
        raise IntakeError(
            f"That is {len((text or '').strip())} characters. Paste the whole "
            f"posting (at least {MIN_PASTED_CHARS}), including the requirements: "
            "drafting tailors to it, and the checks read it for degree and "
            "clearance requirements.")
    url = (url or "").strip()
    if url and urlparse(url).scheme not in ("http", "https"):
        raise IntakeError("The link must start with http:// or https://")
    text = text.strip()
    fingerprint = hashlib.sha256((url or f"{company}|{title}|{text}").encode()).hexdigest()
    job = {
        "external_id": "paste-" + fingerprint[:16],
        "title": title,
        "department": None,
        "location": location.strip(),
        "remote": sources.classify_remote(location, text),
        "employment_type": "unknown",
        "url": url,
        "description": text,
        "description_hash": sources.content_hash(text),
        "posted_at": None,
    }
    return _store(con, job, company=company, slug=_slug(company), kind="manual",
                  source_url=url or "pasted by hand", prefs=prefs)


def enrich(con: sqlite3.Connection, job_id: int) -> str:
    """One enrichment call for the new job: degree, clearance, years, stack.

    The posting text is all the model sees; nothing about the operator goes
    with it. Returns what to tell the operator, including when it did not run.
    """
    from . import enrich as enrich_mod, llm

    try:
        llm.api_key()
    except llm.LLMError:
        return "not checked for degree or clearance requirements: no API key set"
    row = con.execute("SELECT title, description, description_hash FROM jobs "
                      "WHERE id = ?", (job_id,)).fetchone()
    try:
        result = enrich_mod.enrich_one(row["title"], row["description"])
    except Exception as exc:  # noqa: BLE001 - the job is stored either way
        return f"not checked for degree or clearance requirements: {exc}"
    enrich_mod.save(con, job_id, result, enrich_mod.ENRICH_MODELS[0],
                    row["description_hash"])
    facts = []
    if result.degree_required:
        facts.append("asks for a degree")
    if result.clearance_required:
        facts.append("CLEARANCE REQUIRED")
    if result.years_required:
        facts.append(f"{result.years_required}+ yrs")
    return "checked: " + (", ".join(facts) if facts else "no degree, clearance or years requirement found")
