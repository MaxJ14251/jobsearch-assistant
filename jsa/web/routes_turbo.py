"""Turbo (plan 16): one match at a time; pass, interested, the drafting strip.

Moved from jsa/web.py without change (plan 24); see register()."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import approvals
from . import _copies


def register(app, ctx) -> None:
    """Add this area's routes to `app`; shared helpers come from `ctx`."""
    connect = ctx.connect
    learn_state = ctx.learn_state
    learned = ctx.learned
    match_connect = ctx.match_connect
    match_deck = ctx.match_deck
    match_filters = ctx.match_filters
    match_place = ctx.match_place
    new_rows = ctx.new_rows
    profile = ctx.profile
    render = ctx.render

    # --- Turbo (plan 16, ADR 0026): decide interest; never submit ----------

    TURBO_DECK = 100     # cards dealt per page; reloading deals the next ones

    def turbo_strip(con) -> str:
        from .. import turbo
        s = turbo.status(con)
        parts = []
        if s["queued"] + s["running"]:
            parts.append(f"Drafting: {s['running']} running, {s['queued']} waiting")
        if s["done_today"]:
            parts.append(f"{s['done_today']} drafted today")
        if s["pending_review"]:
            parts.append(f"{s['pending_review']} waiting in Review")
        if s["failed"]:
            parts.append(f"{len(s['failed'])} failed")
        if s["model_calls_today"]:
            parts.append(f"{s['model_calls_today']} model call(s) today")
        return " · ".join(parts)

    @app.get("/turbo", response_class=HTMLResponse)
    def turbo_page(request: Request, near: str = "", track: str = "",
                   degree: str = "", remote: str = "", home: str = "",
                   radius: str = "", anywhere: str = "", q: str = "",
                   msg: str = "", bad: int = 0):
        from .. import posting, turbo
        q = q.strip()
        where, params = match_filters(near, track, q, degree, remote)
        kept = [(k, v) for k, v in request.query_params.multi_items()
                if k not in ("msg", "bad")]
        origin, _, _, wanted, _ = match_place(not kept, home, radius, anywhere)
        prof = profile()
        con = match_connect(q)
        try:
            rows = learned(con, new_rows(con, where, params))
            rows, _, _ = match_deck(rows, _copies(con, rows), origin, wanted)
            more = len(rows) > TURBO_DECK
            rows = rows[:TURBO_DECK]
            texts = {r["id"]: r["description"] for r in con.execute(
                "SELECT id, description FROM jobs WHERE id IN (%s)"
                % ",".join("?" * len(rows)), [r["job_id"] for r in rows])} if rows else {}
            limit = turbo.daily_limit(prof)
            left = max(0, limit - turbo.queued_today(con))
            strip = turbo_strip(con)
            learn = learn_state(con, prof)
        finally:
            con.close()
        for r in rows:
            text = texts.get(r["job_id"]) or ""
            r["excerpt"] = posting.visible(text).text[:1200] if text else ""
            r["thin"] = bool(posting.thin(text, r["job_id"]))
        back = urlencode(kept)
        return render("turbo", "turbo", title="Turbo", rows=rows, more=more,
                      back_query=f"?{back}" if back else "", msg=msg, bad=bad,
                      blocker=turbo.drafting_blocker(prof), limit=limit,
                      left_today=left, strip=strip, learn=learn)

    def turbo_reply(js: str, back: str, message: str, bad: bool, **extra):
        """JSON for the page's script; a redirect when JavaScript is off."""
        if js:
            return JSONResponse({"ok": not bad, "message": message, **extra})
        query = back.lstrip("?")
        joiner = "&" if query else ""
        return RedirectResponse(
            f"/turbo?{query}{joiner}{urlencode({'msg': message, 'bad': int(bad)})}",
            status_code=303)

    @app.post("/turbo/pass")
    def turbo_pass(job_id: int = Form(...), back: str = Form(""), js: str = Form("")):
        from .. import turbo
        con = connect()
        try:
            turbo.pass_job(con, job_id)
            con.commit()
        except ValueError as exc:
            return turbo_reply(js, back, str(exc), True)
        finally:
            con.close()
        return turbo_reply(js, back, f"Passed. Undo with `jsa unpass {job_id}`.", False)

    @app.post("/turbo/unpass")
    def turbo_unpass(job_id: int = Form(...), back: str = Form(""), js: str = Form("")):
        from .. import turbo
        con = connect()
        try:
            turbo.unpass(con, job_id)
            con.commit()
        except ValueError as exc:
            return turbo_reply(js, back, str(exc), True)
        finally:
            con.close()
        return turbo_reply(js, back, "Back in your matches.", False)

    @app.post("/turbo/interested")
    def turbo_interested(job_id: int = Form(...), back: str = Form(""),
                         js: str = Form("")):
        """Saves the job (your act) and, within the day's limit, queues its
        drafts. Submits nothing: there is no code path that could."""
        from .. import turbo
        con = connect()
        try:
            result = turbo.interested(con, job_id, profile())
            con.commit()
        except (ValueError, approvals.ApprovalError) as exc:
            return turbo_reply(js, back, str(exc), True)
        finally:
            con.close()
        if result.queued:
            app.state.worker.wake()
        return turbo_reply(js, back, result.message, False, queued=result.queued)

    @app.post("/turbo/cancel")
    def turbo_cancel(back: str = Form(""), js: str = Form("")):
        from .. import turbo
        con = connect()
        try:
            n = turbo.cancel_waiting(con)
            con.commit()
        finally:
            con.close()
        return turbo_reply(js, back, f"Cancelled {n} waiting draft(s).", False)

    @app.get("/turbo/status")
    def turbo_status():
        from .. import turbo
        con = connect()
        try:
            return JSONResponse(turbo.status(con))
        finally:
            con.close()
