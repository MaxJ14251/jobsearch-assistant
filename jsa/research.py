"""What the extension's panel shows about a company and role while you apply
(plan 34; ADR 0031, 0032, 0033).

For the application page's board and posting:

- this posting: what it asks for in education (jsa/degree.py);
- this company: what its postings ask for, from the company's whole public
  board when it was read in the last week, else from the tracker;
- this role, nationwide: who holds it (jsa/roles.py).

"Live" means one thing only: when the company's board hasn't been read in
`FRESH_DAYS`, the dashboard reads that board once, through the same guarded,
allowlisted fetch `jsa find` uses, classifies each posting locally with no
model, and keeps a summary. Its postings are not added to the tracker:
research isn't discovery. No other site is read.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db

BOARDS = ("greenhouse", "lever", "ashby")
FRESH_DAYS = 7
# Board reads a day, across all companies: a person applying to a handful of
# jobs needs a handful; this only stops a runaway loop.
MAX_REFRESHES_PER_DAY = 5


class ResearchError(ValueError):
    """The board could not be read. The message says why, plainly."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp: str | None) -> datetime | None:
    try:
        when = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _fresh(stamp: str | None) -> bool:
    when = _parse(stamp)
    return when is not None and _now() - when < timedelta(days=FRESH_DAYS)


def snapshot(con: sqlite3.Connection, kind: str, board: str) -> sqlite3.Row | None:
    try:
        return con.execute("SELECT * FROM company_degree_snapshots WHERE board_kind = ? "
                           "AND board = ?", (kind, board.lower())).fetchone()
    except sqlite3.OperationalError:          # an older tracker: no table yet
        return None


def _company_for_board(con, kind: str, board: str) -> sqlite3.Row | None:
    from .applyqueue import board_of
    for row in con.execute("SELECT s.url, s.company_id, c.name, c.slug FROM sources s "
                           "JOIN companies c ON c.id = s.company_id WHERE s.kind = ?",
                           (kind,)).fetchall():
        if (board_of(kind, row["url"]) or "").lower() == board.lower():
            return row
    return None


def _profile_from_snapshot(row: sqlite3.Row):
    from .companies import Profile
    profile = Profile(company_id=0, company=row["company"], source="board",
                      as_of=row["fetched_at"])
    profile.n = row["n"]
    profile.unreadable = row["unreadable"]
    profile.counts = json.loads(row["counts_json"] or "{}")
    certs = json.loads(row["certs_json"] or "[]")
    profile.certs = [tuple(c) for c in certs]
    profile.certs_any = row["certs_any"]
    return profile


def research(con: sqlite3.Connection, url: str) -> dict[str, Any]:
    """Everything the panel shows for this application page. Reads only."""
    from . import degree, intake, roles
    from .companies import company_profile

    link = intake.parse_link(url)
    if link is None or link.kind not in BOARDS:
        return {"state": "unsupported",
                "message": "Not a Greenhouse, Lever or Ashby application page."}
    board = link.board.lower()
    out: dict[str, Any] = {
        "board": {"kind": link.kind, "board": board},
        "caveats": {"posting": degree.CAVEAT, "role": roles.CAVEAT},
        "posting": None, "company": None, "role": None,
    }

    snap = snapshot(con, link.kind, board)
    title = None
    job_id = db.job_for_link(con, link)
    company_id = None
    if job_id is not None:
        row = con.execute("SELECT title, company_id, description, degree_level, "
                          "degree_evidence, certs_named FROM jobs WHERE id = ?",
                          (job_id,)).fetchone()
        title, company_id = row["title"], row["company_id"]
        if row["degree_level"] is not None:
            level, evidence = row["degree_level"], row["degree_evidence"] or ""
            certs = json.loads(row["certs_named"] or "[]")
        else:                                      # not read yet: read it now
            facts = degree.classify(row["description"])
            level, evidence, certs = facts.level, facts.evidence, facts.certifications
        out["posting"] = {"label": degree.LABELS[level], "level": level,
                          "evidence": evidence, "certs": certs, "from": "tracker"}
    elif snap is not None:
        found = (json.loads(snap["postings_json"] or "{}")).get(link.job_id.lower())
        if found:
            title = found["title"]
            out["posting"] = {"label": degree.LABELS[found["level"]], "level": found["level"],
                              "evidence": found["evidence"], "certs": found["certs"],
                              "from": "board"}

    if snap is not None and _fresh(snap["fetched_at"]):
        profile = _profile_from_snapshot(snap)
        out["company"] = {"line": profile.line(), "from": "board", "as_of": snap["fetched_at"]}
        state = "ready"
    else:
        if company_id is None:
            known = _company_for_board(con, link.kind, board)
            company_id = known["company_id"] if known else None
        profile = company_profile(con, company_id) if company_id is not None else None
        if profile is not None and profile.n and _fresh(profile.as_of):
            out["company"] = {"line": profile.line(), "from": "tracker",
                              "as_of": profile.as_of}
            state = "ready"
        else:
            if snap is not None:
                out["company"] = {"line": _profile_from_snapshot(snap).line(),
                                  "from": "board", "as_of": snap["fetched_at"]}
            state = "stale" if (snap is not None or profile is not None) else "missing"
    out["state"] = state
    out["can_refresh"] = state in ("stale", "missing") and refreshes_left(con) > 0
    out["role"] = roles.for_title(title) if title else None
    return out


def refreshes_left(con: sqlite3.Connection) -> int:
    since = (_now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        used = con.execute("SELECT COUNT(*) FROM company_degree_snapshots "
                           "WHERE fetched_at >= ?", (since,)).fetchone()[0]
    except sqlite3.OperationalError:
        used = 0
    return max(MAX_REFRESHES_PER_DAY - used, 0)


def refresh(con: sqlite3.Connection, url: str) -> dict[str, Any]:
    """Read this page's board once, if allowed now, and keep a summary.

    At most once per board per `FRESH_DAYS`, and `MAX_REFRESHES_PER_DAY`
    boards a day. Never writes `jobs`."""
    from . import find, intake
    from .degree import classify
    from .posting import THIN_WORDS, prose_words

    link = intake.parse_link(url)
    if link is None or link.kind not in BOARDS:
        raise ResearchError("Not a Greenhouse, Lever or Ashby application page.")
    board = link.board.lower()
    snap = snapshot(con, link.kind, board)
    if snap is not None and _fresh(snap["fetched_at"]):
        return research(con, url)                  # fresh already: no request
    if refreshes_left(con) <= 0:
        raise ResearchError(f"Today's {MAX_REFRESHES_PER_DAY} board reads are used up; "
                            "try again tomorrow.")
    report = find.Report(company=board, title="", city="")
    try:
        result = find._fetch(report, {"kind": link.kind, "board": board})
    except find.HostNotAllowed as exc:
        raise ResearchError(f"Refused: {exc}") from exc
    if not result.ok:
        raise ResearchError("Couldn't read the board just now.")

    counts: Counter = Counter()
    certs: Counter = Counter()
    postings: dict[str, dict] = {}
    n = unreadable = certs_any = 0
    for job in result.jobs:
        facts = classify(job.get("description"))
        postings[str(job.get("external_id") or "").lower()] = {
            "title": job.get("title") or "", "level": facts.level,
            "evidence": facts.evidence, "certs": facts.certifications}
        if prose_words(job.get("description")) < THIN_WORDS:
            unreadable += 1
            continue
        n += 1
        counts[facts.level] += 1
        certs.update(facts.certifications)
        certs_any += bool(facts.certifications)
    known = _company_for_board(con, link.kind, board)
    company = known["name"] if known else board
    con.execute(
        "INSERT INTO company_degree_snapshots (board_kind, board, company, fetched_at, n, "
        "unreadable, certs_any, counts_json, certs_json, postings_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(board_kind, board) DO UPDATE SET "
        "company = excluded.company, fetched_at = excluded.fetched_at, n = excluded.n, "
        "unreadable = excluded.unreadable, certs_any = excluded.certs_any, "
        "counts_json = excluded.counts_json, certs_json = excluded.certs_json, "
        "postings_json = excluded.postings_json",
        (link.kind, board, company, db.utcnow(), n, unreadable, certs_any,
         json.dumps(dict(counts)), json.dumps(certs.most_common(5)),
         json.dumps(postings)))
    con.commit()
    return research(con, url)


def all_snapshots(con: sqlite3.Connection):
    """Every board read, newest first, as profiles (for the Companies page)."""
    try:
        rows = con.execute("SELECT * FROM company_degree_snapshots "
                           "ORDER BY fetched_at DESC").fetchall()
    except sqlite3.OperationalError:
        return []
    return [(_profile_from_snapshot(r), r["board_kind"], r["board"],
             _fresh(r["fetched_at"])) for r in rows]
