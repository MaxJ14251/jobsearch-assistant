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

    It is kept with the decision as the record of why, and `prior_feedback`
    below reads it back to whoever drafts the next version. It is shown to a
    person; it is not fed to a model, and it does not tune anything.
    """
    if not (feedback or "").strip():
        raise ApprovalError(
            "rejection requires feedback saying what to change; "
            "write a short note, then reject again"
        )
    _decide(con, approval_id, "rejected", feedback.strip())


def supersede_older(con: sqlite3.Connection, *, job_id: int, kind: str,
                    version: int) -> list[int]:
    """Close the pending approvals for earlier versions of this document.

    Four of the twelve rejections in the author's tracker read "superseded by
    a later version", and six of eight still-pending ones are the same thing.
    That is bookkeeping, not judgement: v1 stops needing a decision the moment
    v2 exists, and the tool knows that at the moment it writes v2.

    Closed as 'superseded' with decided_by='tool', which a trigger enforces in
    both directions — the tool may not sign as a person, and a person may not
    file a supersede. The audit trail therefore still says exactly who decided
    what, which is the only reason this is safe to automate at all.

    Returns the approval ids closed.
    """
    rows = con.execute(
        "SELECT a.id FROM approvals a JOIN documents d ON d.id = a.subject_id "
        "WHERE a.subject_type = 'document' AND a.decision = 'pending' "
        "AND d.job_id = ? AND d.kind = ? AND d.version < ?",
        (job_id, kind, version),
    ).fetchall()
    closed = [int(r["id"]) for r in rows]
    for approval_id in closed:
        con.execute(
            "UPDATE approvals SET decision = 'superseded', decided_by = 'tool', "
            "decided_at = ?, feedback = ? WHERE id = ?",
            (db.utcnow(), f"superseded by v{version}", approval_id),
        )
    return closed


def prior_feedback(con: sqlite3.Connection, *, job_id: int, kind: str,
                   before_version: int | None = None) -> list[tuple[int, str]]:
    """What a human said about earlier versions of this document.

    Read back to the person drafting the next one, which is the one place it
    would change a decision. Deliberately not passed to a model: it is the
    operator's own words, it can say anything, and this tool does not tune
    itself on it.
    """
    sql = ("SELECT d.version, a.feedback FROM approvals a "
           "JOIN documents d ON d.id = a.subject_id "
           "WHERE a.subject_type = 'document' AND a.decision = 'rejected' "
           "AND a.feedback IS NOT NULL AND TRIM(a.feedback) != '' "
           "AND d.job_id = ? AND d.kind = ?")
    args: list[Any] = [job_id, kind]
    if before_version is not None:
        sql += " AND d.version < ?"
        args.append(before_version)
    return [(int(r["version"]), r["feedback"].strip())
            for r in con.execute(sql + " ORDER BY d.version", args)]


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

    They mean "the latest DRAFTED document of this kind" and move on every
    redraft. They are not what was approved and not what was sent: that is
    submitted_documents, written once by mark_applied (ADR 0012).

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


# A document id that says "this kind was not sent". Document ids start at 1.
NOT_SENT = 0


@dataclass
class Sent:
    """One document as it went out. Read from submitted_documents, never inferred."""
    kind: str
    document_id: int
    version: int
    approved: bool
    sha256: str | None
    submitted_at: str
    path: str | None = None

    def describe(self) -> str:
        label = self.kind.replace("_", " ")
        state = "approved" if self.approved else "NOT approved"
        return f"{label} doc {self.document_id} v{self.version} ({state})"


def _file_sha256(path: str | None) -> str | None:
    import hashlib
    from pathlib import Path

    from .config import ROOT

    if not path:
        return None
    file = Path(path)
    if not file.is_absolute():
        file = ROOT / file
    try:
        return hashlib.sha256(file.read_bytes()).hexdigest()
    except OSError:
        return None


def _choose(
    con: sqlite3.Connection, job_id: int, kind: str, named: int | None,
) -> sqlite3.Row | None:
    """The document of `kind` that went out, or None when none is recorded.

    Named explicitly: that one, whatever its approval, provided it belongs to
    this job and is of this kind. Not named: the newest document a HUMAN
    approved, provided there is exactly one. Never the newest drafted -- that
    is the pointer, and the pointer moves. An unapproved draft is recorded only when the operator names it.
    """
    if named == NOT_SENT:
        return None
    if named is not None:
        row = con.execute(
            "SELECT id, job_id, kind, version, path FROM documents WHERE id = ?",
            (int(named),)).fetchone()
        if row is None:
            raise ApprovalError(f"no document with id {named}")
        if row["job_id"] != int(job_id) or row["kind"] != kind:
            raise ApprovalError(
                f"document {named} is a {row['kind'].replace('_', ' ')} for job "
                f"{row['job_id']}, not a {kind.replace('_', ' ')} for job {job_id}")
        return row
    approved = con.execute(
        "SELECT d.id, d.job_id, d.kind, d.version, d.path FROM documents d "
        "WHERE d.job_id = ? AND d.kind = ? AND EXISTS ("
        "  SELECT 1 FROM approvals a WHERE a.subject_type = 'document' "
        "  AND a.subject_id = d.id AND a.decision = 'approved' "
        "  AND a.decided_by = 'human') "
        "ORDER BY d.version DESC", (int(job_id), kind)).fetchall()
    if len(approved) > 1:
        # A record that can never be changed is not written on a guess.
        flag = "--resume" if kind == "resume" else "--cover"
        choices = ", ".join(f"doc {r['id']} (v{r['version']})" for r in approved)
        raise ApprovalError(
            f"job {job_id} has {len(approved)} approved "
            f"{kind.replace('_', ' ')}s: {choices}. Say which one you sent, "
            f"e.g. {flag} {approved[0]['id']}. Nothing was recorded.")
    return approved[0] if approved else None


def submitted(con: sqlite3.Connection, application_id: int) -> list[Sent]:
    """What went out for this application, as recorded when it was applied."""
    return [
        Sent(kind=r["kind"], document_id=int(r["document_id"]),
             version=int(r["version"]), approved=bool(r["approved"]),
             sha256=r["sha256"], submitted_at=r["submitted_at"], path=r["path"])
        for r in con.execute(
            "SELECT s.*, d.path FROM submitted_documents s "
            "LEFT JOIN documents d ON d.id = s.document_id "
            "WHERE s.application_id = ? ORDER BY s.kind DESC",
            (int(application_id),)).fetchall()
    ]


def unsent_drafts(con: sqlite3.Connection, job_id: int, sent: list[Sent]
                  ) -> list[sqlite3.Row]:
    """The newest draft of each kind that was NOT recorded, so it can be named.

    A pending cover letter is exactly the case: it may or may not have gone
    out, and the tool will not guess.
    """
    recorded = {s.kind for s in sent}
    out = []
    for kind in ("resume", "cover_letter"):
        if kind in recorded:
            continue
        row = con.execute(
            "SELECT id, kind, version FROM documents WHERE job_id = ? AND kind = ? "
            "ORDER BY version DESC LIMIT 1", (int(job_id), kind)).fetchone()
        if row is not None:
            out.append(row)
    return out


def changed_since_approval(con: sqlite3.Connection, item: Sent) -> bool:
    """True when the file on disk was modified after a human approved it.

    A warning, not a refusal: editing your own resume before sending it is
    yours to do. It means the approval no longer covers every word that went
    out, and the record should say so rather than imply it does.
    """
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    from .config import ROOT

    if not item.approved or not item.path:
        return False
    decided = con.execute(
        "SELECT MAX(decided_at) FROM approvals WHERE subject_type = 'document' "
        "AND subject_id = ? AND decision = 'approved' AND decided_by = 'human'",
        (item.document_id,)).fetchone()[0]
    file = Path(item.path)
    if not file.is_absolute():
        file = ROOT / file
    try:
        modified = datetime.fromtimestamp(os.path.getmtime(file), timezone.utc)
    except OSError:
        return False
    return bool(decided) and modified.strftime("%Y-%m-%dT%H:%M:%SZ") > decided


def mark_applied(
    con: sqlite3.Connection, job_id: int, *, when: str | None = None,
    resume: int | None = None, cover: int | None = None,
) -> tuple[int, bool]:
    """Record that a human submitted this application. Returns (id, all_approved).

    Deliberately does NOT require an approved document. Decision 4 of ADR 0003:
    the approval gate exists to stop the AGENT acting autonomously, not to stop
    the human doing what they choose. Someone may apply with a resume this tool
    never generated. A tracker that argues with reality gets abandoned.

    What it does require is that the record be exact (ADR 0012). The documents
    that went out are written to submitted_documents once, here, with their
    approval state and a hash of the file, and later drafting cannot move them.
    `resume` / `cover` name a document id, or NOT_SENT; left out, each kind
    defaults to its human-approved document, to nothing when none is approved,
    and to a refusal when more than one is -- the operator names it. `all_approved` is True only when something was recorded and
    every recorded document was approved -- not when any document for the job
    happens to be.
    """
    application_id = require_application(con, job_id)
    already = con.execute(
        "SELECT applied_at FROM applications WHERE id = ?", (application_id,)
    ).fetchone()["applied_at"]
    recorded = {s.kind: s for s in submitted(con, application_id)}
    if already and resume is None and cover is None:
        raise ApprovalError(
            f"job {job_id} was already recorded as applied on "
            f"{db.local_time(already)}. What "
            "was sent then is fixed. To add a document that is missing from "
            "the record, name it: --resume DOC or --cover DOC.")

    stamp = already or when or db.utcnow()
    sent: list[Sent] = []
    chosen: list[tuple[str, sqlite3.Row]] = []
    for kind, named in (("resume", resume), ("cover_letter", cover)):
        if already and named is None:
            continue
        if kind in recorded:
            if named is None or named == NOT_SENT:
                continue
            raise ApprovalError(
                f"the {kind.replace('_', ' ')} for job {job_id} is already "
                f"recorded as doc {recorded[kind].document_id}. A record of what "
                "was sent can be added to, never changed.")
        row = _choose(con, job_id, kind, named)
        if row is not None:
            chosen.append((kind, row))
    # Everything is chosen before anything is written: a refusal on the second
    # kind must not leave the first behind as half a record.
    for kind, row in chosen:
        doc = int(row["id"])
        item = Sent(kind=kind, document_id=doc, version=int(row["version"]),
                    approved=is_approved(con, "document", doc),
                    sha256=_file_sha256(row["path"]), submitted_at=stamp,
                    path=row["path"])
        con.execute(
            "INSERT INTO submitted_documents (application_id, kind, document_id, "
            "version, approved, sha256, submitted_at) VALUES (?,?,?,?,?,?,?)",
            (application_id, kind, doc, item.version, int(item.approved),
             item.sha256, stamp))
        sent.append(item)

    everything = list(recorded.values()) + sent
    all_approved = bool(everything) and all(s.approved for s in everything)
    if already:
        # An addition to the record, not a second application. The status is
        # wherever the operator has moved it since; it is not reset here.
        return application_id, all_approved
    con.execute(
        "UPDATE applications SET applied_at = ? WHERE id = ?",
        (stamp, application_id),
    )
    if not sent:
        note = "submitted by hand; no approved document on file"
    else:
        note = "submitted by hand: " + "; ".join(s.describe() for s in sent)
    record_event(con, application_id, "applied", actor="human", note=note)
    return application_id, all_approved


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
                seen = db.local_date(row["last_activity_at"])
                quiet = (today - seen).days
            except (TypeError, ValueError):
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
