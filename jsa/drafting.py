"""Draft one document for one job: select, generate, verify, render, queue.

Shared by `jsa tailor` and the dashboard's Tailor control, so both run exactly
the same guards and leave exactly the same trail. It ends by QUEUEING an
approval; it never decides one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import approvals
from . import posting


# Stages a new draft may move to "ready". Everything later is the person's.
DRAFTABLE_STAGES = ("saved", "drafting", "ready")

class DraftError(RuntimeError):
    """The draft was not produced.

    `refused` is True when a fabrication or identity guard fired -- the system
    working, not a crash -- and False for a precondition the reader can fix.
    """

    def __init__(self, message: str, *, refused: bool = False):
        super().__init__(message)
        self.refused = refused


@dataclass
class DraftOutcome:
    path: Path
    document_id: int
    approval_id: int
    version: int
    model: str
    role: str
    bullet_ids: list[str]
    gaps: list[str]
    revert_reasons: dict[str, str] = field(default_factory=dict)
    description_chars: int = 0
    # What the model was not shown of the posting, in words, or "" when it
    # read all of it. Computed here because this is where the posting is.
    description_note: str = ""
    # A warning when the posting has too little text to choose bullets from;
    # the draft is still made (ADR 0005 section 11).
    thin_note: str = ""
    note: str = ""
    letter_problems: list[str] = field(default_factory=list)
    # Approvals the tool closed because this version replaced them, and what
    # a human said about earlier versions. The second is the whole point:
    # feedback was written, stored, displayed on a page nobody was looking at,
    # and read by nothing at the moment it would have changed something.
    superseded: list[int] = field(default_factory=list)
    prior_feedback: list[tuple[int, str]] = field(default_factory=list)
    # The resume report (ADR 0023): advice, shown and stored, never a gate.
    coach: list[Any] = field(default_factory=list)
    # The previous version's bullet ids, when a reviewer rejected it as
    # wrong_bullets (ADR 0024): shown beside the new ones, never to a model.
    last_wrong_bullets: list[str] = field(default_factory=list)


def cover_body(draft, profile: dict[str, Any], job: dict[str, Any]):
    """The letter's text and how it was produced. See ADR 0007.

    This used to concatenate the verified summary and three bullets, with no
    second model call, on the grounds that fresh prose had nothing checking
    it. Read once, that produced resume bullets in a row with no greeting and
    no closing: true, and not a letter. jsa/letter.py writes the prose and
    checks every word of it, falling back to this composition when the drafted
    letter fails a check.
    """
    from . import letter
    return letter.write(job, profile, draft)


def draft_document(
    con: sqlite3.Connection, job_id: int, kind: str, *, force: bool,
    profile: dict[str, Any],
) -> DraftOutcome:
    """Commits on success. Raises DraftError otherwise."""
    letter_problems: list[str] = []
    from . import coach, render
    from .tailor import (
        FabricationError, IdentityLeakError, UndecidedPreferenceError,
        role_kind, tag_weights, vocabulary,
    )
    from .tailor import tailor as build_draft

    job = con.execute(
        "SELECT j.*, c.name AS company FROM jobs j "
        "LEFT JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        raise DraftError(f"no job with id {job_id}")
    job = dict(job)

    try:
        # ADR 0003 decision 1: an application is created explicitly.
        application_id = approvals.require_application(con, job_id)

        existing = render.next_version(con, job_id, kind) - 1
        if existing and not force:
            raise DraftError(
                f"{kind} v{existing} already exists for job {job_id}. "
                f"Re-run with --force to draft v{existing + 1}.")

        # Tag rarity is learned from the postings already in the tracker, so a
        # word appearing in 87% of them cannot outweigh one appearing in 3%.
        weights = tag_weights(con, vocabulary(profile))
        # Not "kind": that name already holds the document kind, and reusing it
        # sent "support" into documents.kind and tripped the CHECK constraint.
        role = role_kind(job.get("title"), job.get("track"))
        draft = build_draft(job, profile, weights=weights)

        version = render.next_version(con, job_id, kind)
        out = render.output_path(job.get("company") or "unknown",
                                 job.get("title") or "role", kind, version)
        out.parent.mkdir(parents=True, exist_ok=True)
        note = ""
        findings = []
        if kind == "resume":
            render.render_resume(draft, profile, job, out)
            findings = coach.review(profile, draft)
        else:
            written = cover_body(draft, profile, job)
            note = written.note
            letter_problems = written.problems
            render.render_cover_letter(draft, profile, job, written.body, out)

        document_id = render.record(
            con, job_id=job_id, kind=kind, path=out, draft=draft,
            prompt_hash=draft.prompt_hash, note=note or None,
            coach=[f.as_dict() for f in findings] if kind == "resume" else None,
        )
        approvals.set_document_pointer(con, application_id, kind, document_id)
        # Only an application not yet sent becomes "ready". Drafting after
        # applying (Turbo's queue, or the Tailor button) must not move an
        # applied, interview or closed application back: that would be the
        # agent deciding a stage (review R-01). The draft is still recorded.
        current = con.execute("SELECT status FROM applications WHERE id = ?",
                              (application_id,)).fetchone()["status"]
        approvals.record_event(
            con, application_id, "ready" if current in DRAFTABLE_STAGES else current,
            actor="agent", note=f"{kind} v{version} drafted, awaiting approval")
        approval_id = approvals.queue(
            con, "document", document_id,
            f"{kind} v{version} for {job.get('title')} at {job.get('company')}")
        # v1 stopped needing a decision the moment v2 was written.
        superseded = approvals.supersede_older(
            con, job_id=job_id, kind=kind, version=version)
        con.commit()
    except (approvals.ApprovalError, UndecidedPreferenceError) as exc:
        raise DraftError(str(exc)) from exc
    except (FabricationError, IdentityLeakError, render.RenderError) as exc:
        raise DraftError(str(exc), refused=True) from exc

    return DraftOutcome(
        path=out, document_id=document_id, approval_id=approval_id,
        version=version, model=draft.model, role=role,
        bullet_ids=[b.source_id for b in draft.bullets],
        gaps=list(draft.keywords_missing),
        revert_reasons=dict(draft.revert_reasons),
        description_chars=len(job.get("description") or ""),
        description_note=posting.note(posting.visible(job.get("description"))),
        thin_note=posting.thin(job.get("description"), job_id),
        note=note, letter_problems=letter_problems,
        superseded=superseded,
        prior_feedback=approvals.prior_feedback(
            con, job_id=job_id, kind=kind, before_version=version),
        coach=findings,
        last_wrong_bullets=wrong_bullets_before(con, job_id, kind, version),
    )


def wrong_bullets_before(con: sqlite3.Connection, job_id: int, kind: str,
                         version: int) -> list[str]:
    """Bullet ids of version - 1, if a reviewer rejected it as wrong_bullets."""
    import json

    row = con.execute(
        "SELECT d.bullet_ids FROM documents d JOIN approvals a "
        "ON a.subject_type = 'document' AND a.subject_id = d.id "
        "WHERE d.job_id = ? AND d.kind = ? AND d.version = ? "
        "AND a.reason = 'wrong_bullets' ORDER BY a.id DESC LIMIT 1",
        (job_id, kind, version - 1)).fetchone()
    try:
        return list(json.loads(row["bullet_ids"] or "[]")) if row else []
    except ValueError:
        return []
