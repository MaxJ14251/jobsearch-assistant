"""The human approval gate.

Nothing leaves this machine without a human saying so. That is enforced in the
database by `trg_approval_requires_human`, not by convention here — an approval
row can only move to approved/rejected when `decided_by = 'human'` and
`decided_at` is set, so the guarantee survives a bug in this file.

This module is the *only* place that writes a decision, and it refuses to run
outside an interactive invocation. `tests/test_approvals.py` asserts the
database-level block directly, the way a buggy agent would trip it.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from . import db

SubjectType = Literal["application", "outreach", "document"]


class ApprovalError(RuntimeError):
    """A decision was attempted improperly."""


@dataclass
class PendingItem:
    approval_id: int
    subject_type: str
    subject_id: int
    summary: str
    requested_at: str


def queue(
    con: sqlite3.Connection, subject_type: SubjectType, subject_id: int,
    summary: str,
) -> int:
    """Ask a human to decide. Always starts as 'pending'."""
    cur = con.execute(
        "INSERT INTO approvals (subject_type, subject_id, summary) "
        "VALUES (?,?,?)",
        (subject_type, int(subject_id), summary),
    )
    return int(cur.lastrowid)


def pending(con: sqlite3.Connection) -> list[PendingItem]:
    return [
        PendingItem(
            approval_id=r["approval_id"], subject_type=r["subject_type"],
            subject_id=r["subject_id"], summary=r["summary"],
            requested_at=r["requested_at"],
        )
        for r in con.execute("SELECT * FROM v_awaiting_approval").fetchall()
    ]


def _decide(
    con: sqlite3.Connection, approval_id: int, decision: str,
    feedback: str | None,
) -> None:
    row = con.execute(
        "SELECT decision FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    if row is None:
        raise ApprovalError(f"no approval with id {approval_id}")
    if row["decision"] != "pending":
        raise ApprovalError(
            f"approval {approval_id} was already {row['decision']}; "
            "re-render to create a new one rather than reusing this decision"
        )
    con.execute(
        "UPDATE approvals SET decision = ?, decided_by = 'human', "
        "decided_at = ?, feedback = ? WHERE id = ?",
        (decision, db.utcnow(), feedback, approval_id),
    )


def approve(con: sqlite3.Connection, approval_id: int, note: str | None = None) -> None:
    """Record a human approval. Only ever called from an interactive command."""
    _decide(con, approval_id, "approved", note)


def reject(con: sqlite3.Connection, approval_id: int, feedback: str) -> None:
    """Record a human rejection. Feedback is required.

    It is kept with the decision as the record of why. Nothing reads it back
    into a redraft yet; an earlier message here claimed otherwise.
    """
    if not (feedback or "").strip():
        raise ApprovalError(
            "rejection requires feedback saying what to change; "
            "write a short note, then reject again"
        )
    _decide(con, approval_id, "rejected", feedback.strip())


def is_approved(
    con: sqlite3.Connection, subject_type: SubjectType, subject_id: int
) -> bool:
    """True only for a standing human approval. Used as a gate before any send."""
    row = con.execute(
        "SELECT 1 FROM approvals WHERE subject_type = ? AND subject_id = ? "
        "AND decision = 'approved' AND decided_by = 'human' LIMIT 1",
        (subject_type, int(subject_id)),
    ).fetchone()
    return row is not None


# --- application lifecycle ---------------------------------------------------
# See docs/decisions/0003-application-lifecycle.md.

DOC_POINTERS = {"resume": "resume_doc_id", "cover_letter": "cover_doc_id"}


def save_application(
    con: sqlite3.Connection, job_id: int, *, note: str | None = None
) -> tuple[int, bool]:
    """Create the application for a job, or return the existing one.

    Returns (application_id, created). applications.job_id is NOT NULL UNIQUE,
    so a second save cannot produce a duplicate -- it returns the first.
    """
    row = con.execute(
        "SELECT id FROM applications WHERE job_id = ?", (int(job_id),)
    ).fetchone()
    if row is not None:
        return int(row["id"]), False

    if con.execute("SELECT 1 FROM jobs WHERE id = ?", (int(job_id),)).fetchone() is None:
        raise ApprovalError(f"no job with id {job_id}")

    cur = con.execute(
        "INSERT INTO applications (job_id, status) VALUES (?, 'saved')",
        (int(job_id),),
    )
    application_id = int(cur.lastrowid)
    # record_event writes the event and syncs status; 'saved' is already the
    # column default, so this exists for the audit trail, not the status.
    record_event(con, application_id, "saved", actor="human", note=note)
    return application_id, True


def require_application(con: sqlite3.Connection, job_id: int) -> int:
    """The application id for a job, or a refusal naming what to run.

    Decision 1 of ADR 0003: an application is created explicitly. Tailoring a
    job nobody saved would fill the pipeline with roles never pursued.
    """
    row = con.execute(
        "SELECT id FROM applications WHERE job_id = ?", (int(job_id),)
    ).fetchone()
    if row is None:
        raise ApprovalError(
            f"job {job_id} has not been saved as an application. "
            f"Run:  jsa save {job_id}"
        )
    return int(row["id"])


def set_document_pointer(
    con: sqlite3.Connection, application_id: int, kind: str, document_id: int
) -> None:
    """The ONLY writer of applications.resume_doc_id / cover_doc_id.

    Those columns are a denormalized cache of "the current document of this
    kind" -- the schema says so, and says the app layer keeps them in sync.
    That phrase is how two copies of a fact drift apart, so there is one writer
    and it refuses to point an application at another job's document.
    """
    column = DOC_POINTERS.get(kind)
    if column is None:
        raise ApprovalError(
            f"{kind!r} has no pointer column; expected one of "
            f"{', '.join(sorted(DOC_POINTERS))}"
        )
    doc = con.execute(
        "SELECT job_id FROM documents WHERE id = ?", (int(document_id),)
    ).fetchone()
    if doc is None:
        raise ApprovalError(f"no document with id {document_id}")
    app = con.execute(
        "SELECT job_id FROM applications WHERE id = ?", (int(application_id),)
    ).fetchone()
    if app is None:
        raise ApprovalError(f"no application with id {application_id}")
    if doc["job_id"] != app["job_id"]:
        raise ApprovalError(
            f"document {document_id} belongs to job {doc['job_id']}, but "
            f"application {application_id} is for job {app['job_id']}. "
            "A pointer across jobs is corruption, not a shortcut."
        )
    con.execute(
        f"UPDATE applications SET {column} = ? WHERE id = ?",
        (int(document_id), int(application_id)),
    )


def mark_applied(
    con: sqlite3.Connection, job_id: int, *, when: str | None = None
) -> tuple[int, bool]:
    """Record that a human submitted this application. Returns (id, had_approval).

    Deliberately does NOT require an approved document. Decision 4 of ADR 0003:
    the approval gate exists to stop the AGENT acting autonomously, not to stop
    the human doing what they choose. Someone may apply with a resume this tool
    never generated. A tracker that argues with reality gets abandoned.

    It reports whether an approved document existed so the caller can say so,
    and the event note records it either way.
    """
    application_id = require_application(con, job_id)
    approved = any(
        is_approved(con, "document", int(r["id"]))
        for r in con.execute(
            "SELECT id FROM documents WHERE job_id = ?", (int(job_id),)
        ).fetchall()
    )
    con.execute(
        "UPDATE applications SET applied_at = ? WHERE id = ?",
        (when or db.utcnow(), application_id),
    )
    record_event(
        con, application_id, "applied", actor="human",
        note=("submitted by hand" if approved
              else "submitted by hand; no approved document on file"),
    )
    return application_id, approved


def record_event(
    con: sqlite3.Connection, application_id: int, to_status: str,
    *, actor: str = "agent", note: str | None = None,
) -> None:
    from_status = con.execute(
        "SELECT status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    con.execute(
        "INSERT INTO application_events (application_id, from_status, to_status, "
        "actor, note) VALUES (?,?,?,?,?)",
        (application_id, from_status["status"] if from_status else None,
         to_status, actor, note),
    )
    con.execute(
        "UPDATE applications SET status = ? WHERE id = ?",
        (to_status, application_id),
    )


# --- stages after "ready" ----------------------------------------------------
# ADR 0003 modelled the whole journey and only two stages were reachable, so
# every application in the tracker sat at 'ready' forever. These move an
# application the rest of the way. Every one of them is a HUMAN action: the
# tool cannot observe a phone screen, and it may never decide one happened.

# MUST stay a subset of the CHECK constraint on applications.status in
# db/schema.sql. The seniority vocabulary drifted from its CHECK once and
# killed an enrichment run mid-pass; a test parses the schema and asserts
# these agree.
STAGES = (
    "saved", "drafting", "ready", "applied",
    "phone_screen", "technical", "onsite", "offer",
    "rejected", "withdrawn", "ghosted",
)

# Stages that are over. A closed application keeps its history and leaves the
# live pipeline.
CLOSED = frozenset({"rejected", "withdrawn", "ghosted"})

# After this long with no event, an application is worth a look. It is a
# REPORT, never a status change: the tool does not decide you were ghosted.
QUIET_DAYS = 21


def set_stage(
    con: sqlite3.Connection, job_id: int, stage: str, *, note: str | None = None,
) -> tuple[int, str]:
    """Move an application to `stage`. Returns (application_id, previous).

    'applied' routes through mark_applied so ADR 0003 decision 4 still holds:
    it records the submission, warns when no approved document exists, and
    refuses nothing.
    """
    if stage not in STAGES:
        raise ApprovalError(
            f"{stage!r} is not a stage. Use one of: {', '.join(STAGES)}"
        )
    application_id = require_application(con, job_id)
    previous = con.execute(
        "SELECT status FROM applications WHERE id = ?", (application_id,)
    ).fetchone()["status"]
    if stage == "applied":
        mark_applied(con, job_id)
        if note:
            con.execute(
                "UPDATE application_events SET note = note || ' — ' || ? "
                "WHERE id = (SELECT MAX(id) FROM application_events "
                "WHERE application_id = ?)", (note, application_id))
        return application_id, previous
    record_event(con, application_id, stage, actor="human", note=note)
    return application_id, previous


def set_next_action(
    con: sqlite3.Connection, job_id: int, action: str, *, due: str | None = None,
) -> int:
    """Record what you intend to do next, and when it is due.

    Not an event: an intention is not something that happened, and writing one
    would move the application's status. `due` is a plain date (YYYY-MM-DD)
    because that is what a follow-up is measured in.
    """
    if not (action or "").strip():
        raise ApprovalError("a next action needs text saying what to do")
    if due is not None:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due.strip()):
            raise ApprovalError(f"due date {due!r} is not YYYY-MM-DD")
        try:
            date.fromisoformat(due.strip())
        except ValueError as exc:
            raise ApprovalError(f"due date {due!r} is not a real date") from exc
    application_id = require_application(con, job_id)
    con.execute(
        "UPDATE applications SET next_action = ?, next_action_due = ? "
        "WHERE id = ?",
        (action.strip(), (due or "").strip() or None, application_id),
    )
    return application_id


@dataclass
class DueItem:
    job_id: int
    application_id: int
    title: str
    company: str
    status: str
    next_action: str | None
    due: str | None
    days_out: int | None          # negative when overdue, None when no date
    last_activity_at: str | None
    quiet_days: int | None

    @property
    def overdue(self) -> bool:
        return self.days_out is not None and self.days_out < 0


def due_items(con: sqlite3.Connection, *, days: int = 7,
              today: date | None = None) -> list[DueItem]:
    """What needs attention: due or overdue actions first, then gone quiet.

    An application with no next action and no event for QUIET_DAYS is listed
    last, as a question rather than a verdict.
    """
    today = today or date.today()
    rows = con.execute(
        "SELECT a.id AS application_id, a.job_id, a.status, a.next_action, "
        "       a.next_action_due, a.last_activity_at, j.title, c.name AS company "
        "  FROM applications a "
        "  JOIN jobs j ON j.id = a.job_id "
        "  LEFT JOIN companies c ON c.id = j.company_id "
        " WHERE a.archived_at IS NULL AND a.status NOT IN "
        f"       ({', '.join('?' * len(CLOSED))})",
        tuple(sorted(CLOSED)),
    ).fetchall()

    items: list[DueItem] = []
    for row in rows:
        days_out = None
        if row["next_action_due"]:
            try:
                days_out = (date.fromisoformat(row["next_action_due"]) - today).days
            except ValueError:
                days_out = None
        quiet = None
        if row["last_activity_at"]:
            try:
                seen = date.fromisoformat(row["last_activity_at"][:10])
                quiet = (today - seen).days
            except ValueError:
                quiet = None
        item = DueItem(
            job_id=row["job_id"], application_id=row["application_id"],
            title=row["title"], company=row["company"] or "unknown",
            status=row["status"], next_action=row["next_action"],
            due=row["next_action_due"], days_out=days_out,
            last_activity_at=row["last_activity_at"], quiet_days=quiet,
        )
        if days_out is not None and days_out <= days:
            items.append(item)
        elif days_out is None and (quiet or 0) >= QUIET_DAYS:
            items.append(item)
    # Overdue first, then soonest; anything without a date goes last, quietest
    # first.
    items.sort(key=lambda i: (i.days_out is None,
                              i.days_out if i.days_out is not None else 0,
                              -(i.quiet_days or 0)))
    return items
