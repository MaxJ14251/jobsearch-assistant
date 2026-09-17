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


def cover_body(draft) -> str:
    """Compose the letter from the ALREADY-VERIFIED draft.

    Deliberately no second model call. Every sentence below has passed
    verify_draft; asking a model for fresh prose here would open a fabrication
    surface that nothing downstream checks.
    """
    parts = [draft.summary] + [b.text for b in draft.bullets[:3]]
    return "\n\n".join(part for part in parts if part and part.strip())


def draft_document(
    con: sqlite3.Connection, job_id: int, kind: str, *, force: bool,
    profile: dict[str, Any],
) -> DraftOutcome:
    """Commits on success. Raises DraftError otherwise."""
    from . import render
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
        if kind == "resume":
            render.render_resume(draft, profile, job, out)
        else:
            render.render_cover_letter(draft, profile, job, cover_body(draft), out)

        document_id = render.record(
            con, job_id=job_id, kind=kind, path=out, draft=draft,
            prompt_hash=draft.prompt_hash,
        )
        approvals.set_document_pointer(con, application_id, kind, document_id)
        approvals.record_event(
            con, application_id, "ready", actor="agent",
            note=f"{kind} v{version} drafted, awaiting approval")
        approval_id = approvals.queue(
            con, "document", document_id,
            f"{kind} v{version} for {job.get('title')} at {job.get('company')}")
        con.commit()
    except (approvals.ApprovalError, UndecidedPreferenceError) as exc:
        raise DraftError(str(exc)) from exc
    except (FabricationError, IdentityLeakError) as exc:
        raise DraftError(str(exc), refused=True) from exc

    return DraftOutcome(
        path=out, document_id=document_id, approval_id=approval_id,
        version=version, model=draft.model, role=role,
        bullet_ids=[b.source_id for b in draft.bullets],
        gaps=list(draft.keywords_missing),
        revert_reasons=dict(draft.revert_reasons),
        description_chars=len(job.get("description") or ""),
    )
