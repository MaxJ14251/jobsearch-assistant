"""The review queue: approve or reject a draft.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

import json
from urllib.parse import urlencode

from fastapi import Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import approvals


def _flagged(item: dict) -> bool:
    """A draft that says something the profile does not, or can't be read.
    A cover letter's note alone is not a flag: it says the drafted prose was
    refused and the letter was built from the profile's own sentences
    (2026-10-27: all 9 notes on the queue were that)."""
    doc = item.get("doc")
    if not doc:
        return False
    if doc.get("problem") or (doc.get("compare") or {}).get("flagged"):
        return True
    return doc.get("kind") == "cover_letter" and bool((doc.get("letter") or {}).get("problems"))


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    inspect_document = ctx.inspect_document
    profile = ctx.profile
    render = ctx.render

    @app.get("/review", response_class=HTMLResponse)
    def review_queue(error: str = "", error_id: int = 0):
        prof = profile()
        con = connect()
        try:
            rows = []
            for r in con.execute("SELECT * FROM v_awaiting_approval").fetchall():
                item = dict(r)
                if r["subject_type"] == "document":
                    d = con.execute("SELECT * FROM documents WHERE id = ?",
                                    (r["subject_id"],)).fetchone()
                    item["doc"] = inspect_document(d, prof) if d else {
                        "problem": "This draft's record is missing.",
                        "servable": False, "paragraphs": [], "compare": None,
                        "job_id": None}
                    # The resume report, as it was when this was drafted.
                    try:
                        item["coach"] = json.loads(d["coach_findings"] or "[]") if d else []
                    except (ValueError, IndexError, KeyError):
                        item["coach"] = []
                elif r["subject_type"] == "outreach":
                    o = con.execute("SELECT draft_body FROM outreach WHERE id = ?",
                                    (r["subject_id"],)).fetchone()
                    item["body"] = o["draft_body"] if o else None
                rows.append(item)
        finally:
            con.close()
        # With 40 waiting (2026-10-27) the 9 flagged drafts were mixed in.
        # Jobs with a flagged draft come first, the flagged draft leading;
        # each job's resume and cover letter stay together.
        for item in rows:
            item["flagged"] = _flagged(item)
        flagged_jobs = {(r.get("doc") or {}).get("job_id") for r in rows if r["flagged"]}
        rows = [r for _, r in sorted(enumerate(rows), key=lambda pair: (
            (pair[1].get("doc") or {}).get("job_id") not in flagged_jobs,
            not pair[1]["flagged"], pair[0]))]
        # The resume report is advice about the profile, not about a draft:
        # shown once, every distinct point, instead of on every card.
        advice, seen = [], set()
        for item in rows:
            for f in item.get("coach") or []:
                key = (f.get("id"), f.get("message"), f.get("where"))
                if key not in seen:
                    seen.add(key)
                    advice.append(f)
        return render("review", "review", title="Review", rows=rows, advice=advice,
                      flagged=[r for r in rows if r["flagged"]],
                      reject_reasons=approvals.REJECT_REASONS,
                      error=error, error_id=error_id,
                      error_in_rows=any(r["approval_id"] == error_id for r in rows))

    def back_to_review(error: str = "", approval_id: int = 0) -> RedirectResponse:
        if not error:
            return RedirectResponse("/review", status_code=303)
        query = urlencode({"error": error, "error_id": approval_id})
        return RedirectResponse(f"/review?{query}", status_code=303)

    # Both call exactly what `jsa approve` / `jsa reject` call. ADR 0003
    # decision 5: one path to a human decision.
    @app.post("/approve")
    def do_approve(approval_id: int = Form(...)):
        con = connect()
        try:
            approvals.approve(con, approval_id)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_review(str(exc), approval_id)
        finally:
            con.close()
        return back_to_review()

    @app.post("/reject")
    def do_reject(approval_id: int = Form(...), feedback: str = Form(""),
                  reason: str = Form("")):
        con = connect()
        try:
            approvals.reject(con, approval_id, feedback, reason or None)
            con.commit()
        except approvals.ApprovalError as exc:
            return back_to_review(str(exc), approval_id)
        finally:
            con.close()
        return back_to_review()
