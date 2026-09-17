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

import sqlite3
from dataclasses import dataclass
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
