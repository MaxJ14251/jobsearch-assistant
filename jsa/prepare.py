"""Add, save and draft a resume from the extension's panel (ADR 0031).

On an application page for a job that isn't ready yet, the panel offers one
button. Pressing it is the person's request, and this does what the
dashboard's Add page, Save button and resume button do, in that order:

- the posting is read from the board's own API (`intake.add_link`), never
  from the page the person is on, and no enrichment call is made;
- the job is saved, the person's act, as the Save button records it;
- one resume is drafted (`drafting.draft_document`), checked like any other,
  and queued in Review.

Approval stays in the dashboard, where the resume can be read: nothing here
approves, and nothing is submitted. A posting with almost no text is saved
but not drafted (as Turbo does), and drafting is capped per day.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import approvals, db, intake, posting

EXT_DRAFT_DAILY = 10      # resumes per 24 hours; profile: extension.daily_drafts
BOARDS = ("greenhouse", "lever", "ashby")
# Stages a resume may still be drafted for: before anything was sent.
OPEN_STAGES = ("saved", "drafting", "ready")


def daily_limit(profile: dict[str, Any] | None) -> int:
    value = ((profile or {}).get("extension") or {}).get("daily_drafts")
    return value if isinstance(value, int) and value >= 0 else EXT_DRAFT_DAILY


def left_today(con: sqlite3.Connection, profile: dict[str, Any] | None) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    used = con.execute("SELECT COUNT(*) FROM ext_drafts WHERE requested_at >= ?",
                       (since,)).fetchone()[0]
    return max(daily_limit(profile) - used, 0)


def _job(con: sqlite3.Connection, job_id: int) -> tuple[dict[str, Any], sqlite3.Row]:
    row = con.execute("SELECT j.id, j.title, j.description, c.name AS company FROM jobs j "
                      "LEFT JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
                      (job_id,)).fetchone()
    return {"id": row["id"], "title": row["title"], "company": row["company"]}, row


def prepare(con: sqlite3.Connection, url: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Add (if needed), save and draft a resume for the job behind `url`.

    Returns {"state", "message", ...}; states: drafted, saved, exists, past,
    unsupported, failed. Commits what it did, even when drafting fails."""
    from .config import Preferences
    from .drafting import DraftError, draft_document
    from .llm import LLMError
    from .turbo import drafting_blocker

    link = intake.parse_link(url)
    if link is None or link.kind not in BOARDS:
        return {"state": "unsupported", "message": "This page is not a Greenhouse, Lever "
                                                   "or Ashby application."}
    job_id = db.job_for_link(con, link)
    added = False
    if job_id is None:
        try:
            result = intake.add_link(con, url, Preferences.from_profile(profile))
        except (intake.IntakeError, OSError) as exc:
            return {"state": "failed", "message": f"Couldn't add this job: {exc}"}
        job_id, added = result.job_id, result.new
    summary, row = _job(con, job_id)
    application_id, _ = approvals.save_application(con, job_id)
    con.commit()
    status = con.execute("SELECT status FROM applications WHERE id = ?",
                         (application_id,)).fetchone()["status"]
    done = "Added and saved" if added else "Saved"
    if status not in OPEN_STAGES:
        return {"state": "past", "job": summary,
                "message": f"This application is already {status.replace('_', ' ')}."}
    existing = con.execute("SELECT MAX(version) FROM documents WHERE job_id = ? "
                           "AND kind = 'resume'", (job_id,)).fetchone()[0]
    if existing:
        return {"state": "exists", "job": summary,
                "message": f"{done}. A resume (v{existing}) already exists for this job: "
                           "approve it in Review, then reload this page."}
    blocker = drafting_blocker(profile)
    if blocker:
        return {"state": "saved", "job": summary,
                "message": f"{done}, not drafted: {blocker}."}
    if posting.thin(row["description"], job_id):
        return {"state": "saved", "job": summary,
                "message": f"{done}, not drafted: this posting has almost no text. "
                           "Fill it in from the job page, then draft."}
    if left_today(con, profile) <= 0:
        return {"state": "saved", "job": summary,
                "message": f"{done}, not drafted: today's limit of "
                           f"{daily_limit(profile)} resumes from the extension is "
                           "reached. Draft it from the job page."}
    con.execute("INSERT INTO ext_drafts (job_id) VALUES (?)", (job_id,))
    con.commit()
    try:
        outcome = draft_document(con, job_id, "resume", force=False, profile=profile)
    except DraftError as exc:
        prefix = "refused by a safety check: " if exc.refused else ""
        return {"state": "saved", "job": summary,
                "message": f"{done}, not drafted: {prefix}{exc}"}
    except LLMError as exc:
        return {"state": "saved", "job": summary,
                "message": f"{done}, not drafted: the model call failed: {exc}"}
    return {"state": "drafted", "job": summary, "version": outcome.version,
            "message": f"{done}, and resume v{outcome.version} drafted. Read and "
                       "approve it in Review, then reload this page to fill the form."}
