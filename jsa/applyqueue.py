"""The apply session's queue (plan 31, ADR 0031): which saved jobs are ready
to apply to, in order, and the form to open for each.

Reads only. The session itself lives in the browser extension, which opens
each form and fills it; the person presses each Submit, and every step to
the next job is their click.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from . import approvals

# Boards the extension can fill (plan 30). Anything else is applied to by hand.
SUPPORTED = ("greenhouse", "lever", "ashby")
STAGES = ("saved", "drafting", "ready")    # web.APPLY_BY_HAND_STAGES

# The board token, from the API address a source was polled at. The company's
# slug is not it: Rocket Lab is `rocket-lab` in the tracker, `rocketlab` on
# Greenhouse.
_BOARD = {
    "greenhouse": re.compile(r"boards-api\.greenhouse\.io/v1/boards/([^/?#]+)"),
    "lever": re.compile(r"api\.lever\.co/v0/postings/([^/?#]+)"),
    "ashby": re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([^/?#]+)"),
}


def board_of(kind: str, source_url: str | None) -> str | None:
    pattern = _BOARD.get(kind)
    match = pattern.search(source_url or "") if pattern else None
    return match.group(1) if match else None


def apply_url(kind: str, board: str, external_id: str) -> str | None:
    """The board's own application form, never the employer's careers page.

    Checked 2026-10-07 with one GET each: the Greenhouse form is served on
    job-boards.greenhouse.io even for an employer that embeds it on its own
    site (SpaceX), so the embed address is not needed."""
    if not board or not external_id:
        return None
    if kind == "greenhouse" and str(external_id).isdigit():
        return f"https://job-boards.greenhouse.io/{board}/jobs/{external_id}"
    if kind == "lever":
        return f"https://jobs.lever.co/{board}/{external_id}/apply"
    if kind == "ashby":
        return f"https://jobs.ashbyhq.com/{board}/{external_id}/application"
    return None


def _status(con: sqlite3.Connection, document_id: int) -> str:
    """The latest decision on a document. "approved" only when it is a
    person's standing approval, checked by the same gate every send uses."""
    row = con.execute(
        "SELECT decision FROM approvals WHERE subject_type = 'document' "
        "AND subject_id = ? ORDER BY id DESC LIMIT 1", (document_id,)).fetchone()
    if row is None:
        return "not queued"
    if row["decision"] == "approved" and not approvals.is_approved(con, "document",
                                                                   document_id):
        return "not queued"
    return row["decision"]


def ready_to_apply(con: sqlite3.Connection) -> dict[str, Any]:
    """{items: [...], left_out: {needs_approval, by_hand, closed}}.

    Ready: an application still being applied to by hand, on a board the
    extension fills, whose job is open and which has a resume a person
    approved. Highest match first; ties go to the resume approved earliest.
    """
    rows = con.execute(
        "SELECT a.job_id, j.title, c.name AS company, j.match_score, j.external_id, "
        "j.closed_at, j.archived_at, s.kind, s.url AS source_url "
        "FROM applications a JOIN jobs j ON j.id = a.job_id "
        "LEFT JOIN companies c ON c.id = j.company_id "
        "LEFT JOIN sources s ON s.id = j.source_id "
        "WHERE a.status IN (%s)" % ",".join("?" * len(STAGES)), STAGES).fetchall()
    items, left_out = [], {"needs_approval": 0, "by_hand": 0, "closed": 0}
    for r in rows:
        if r["closed_at"] or r["archived_at"]:
            left_out["closed"] += 1
            continue
        board = board_of(r["kind"] or "", r["source_url"])
        url = apply_url(r["kind"] or "", board or "", r["external_id"] or "") \
            if r["kind"] in SUPPORTED else None
        if url is None:
            left_out["by_hand"] += 1
            continue
        docs = con.execute(
            "SELECT id, version FROM documents WHERE job_id = ? AND kind = 'resume' "
            "ORDER BY version DESC, id DESC", (r["job_id"],)).fetchall()
        statuses = [(d, _status(con, d["id"])) for d in docs]
        approved = [d for d, s in statuses if s == "approved"]
        if not approved:
            left_out["needs_approval"] += 1
            continue
        newest = approved[0]
        when = con.execute(
            "SELECT MIN(decided_at) AS at FROM approvals WHERE subject_type = 'document' "
            "AND subject_id = ? AND decision = 'approved'",
            (newest["id"],)).fetchone()["at"] or ""
        warnings = []
        if any(s == "pending" and d["version"] > newest["version"] for d, s in statuses):
            warnings.append(f"A newer resume draft is waiting in Review; the session "
                            f"fills the approved v{newest['version']}.")
        items.append({
            "job_id": r["job_id"], "title": r["title"], "company": r["company"] or "",
            "kind": r["kind"], "external_id": str(r["external_id"]),
            "apply_url": url, "resume_version": newest["version"],
            "warnings": warnings, "_order": (-(r["match_score"] or 0), when, r["job_id"]),
        })
    items.sort(key=lambda i: i["_order"])
    for item in items:
        del item["_order"]
    return {"items": items, "left_out": left_out}
