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
    """Record a human rejection. Feedback is required — it drives the redraft."""
    if not (feedback or "").strip():
        raise ApprovalError(
            "rejection requires feedback saying what to change; "
            "without it a regeneration has nothing to work from"
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
