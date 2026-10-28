"""Today: the redesign's home page (2026-10-27). One next step, what is
waiting on you, three numbers and the newest strong matches. It reads only;
every action it points to lives on its own page."""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi.responses import HTMLResponse

from .. import approvals

# Matches at or above this are "strong", as on Pipeline's empty-state note.
from .routes_pipeline import STRONG_MATCH


def next_step(counts: dict[str, int]) -> dict[str, str]:
    """The one thing most worth doing now, in the order a job search moves:
    a reply can change a stage, a draft blocks applying, a ready job is the
    point of it all, a due follow-up is a promise, saved jobs need drafts,
    and new strong matches need a decision."""
    n = counts
    if n["replies"]:
        return {"text": f"Confirm {plural_n(n['replies'], 'email reply', 'email replies')}",
                "href": "/replies", "action": "Open Replies"}
    if n["drafts"]:
        return {"text": f"Approve {plural_n(n['drafts'], 'draft')} so you can apply",
                "href": "/review", "action": "Open Review"}
    if n["ready"]:
        return {"text": f"Apply to {plural_n(n['ready'], 'ready job')}",
                "href": "/pipeline", "action": "Open Pipeline"}
    if n["due"]:
        return {"text": f"{plural_n(n['due'], 'follow-up')} due",
                "href": "/pipeline#first-due", "action": "Open Pipeline"}
    if n["outreach"]:
        return {"text": f"Approve {plural_n(n['outreach'], 'outreach message')}",
                "href": "/review", "action": "Open Review"}
    if n["undrafted"]:
        return {"text": f"Draft {plural_n(n['undrafted'], 'saved job')}",
                "href": "/pipeline", "action": "Open Pipeline"}
    if n["strong"]:
        # Nationwide, as the count is: a bare "/" applies the home radius
        # and showed far fewer (arena M3).
        return {"text": f"Look through {plural_n(n['strong'], 'strong new match', 'strong new matches')}",
                "href": "/?anywhere=1", "action": "Open Matches"}
    return {"text": "You're caught up", "href": "/", "action": "Browse matches"}


def plural_n(n: int, one: str, many: str | None = None) -> str:
    from . import plural
    return plural(n, one, many)


def register(app, ctx) -> None:
    connect = ctx.connect
    render = ctx.render

    @app.get("/today", response_class=HTMLResponse)
    def today_page():
        from .. import applyqueue, turbo
        con = connect()
        try:
            one = lambda sql, *a: con.execute(sql, a).fetchone()[0]  # noqa: E731
            try:
                replies = one("SELECT COUNT(*) FROM inbox_replies WHERE state = 'pending'")
            except sqlite3.OperationalError:          # an older tracker: no inbox yet
                replies = 0
            counts = {
                "replies": replies,
                # Documents only: an outreach note waiting is not a draft
                # that stands between you and applying.
                "drafts": one("SELECT COUNT(*) FROM approvals WHERE decision = 'pending' "
                              "AND subject_type = 'document'"),
                "ready": len(applyqueue.ready_to_apply(con)["items"]),
                "due": sum(1 for d in approvals.due_items(con, days=0)
                           if d.days_out is not None and d.days_out <= 0),
                "undrafted": len(turbo.batch_candidates(con)[0]),
                "strong": one("SELECT COUNT(*) FROM v_new_matches WHERE match_score >= ?",
                              STRONG_MATCH),
                "outreach": one("SELECT COUNT(*) FROM approvals WHERE decision = 'pending' "
                                "AND subject_type = 'outreach'"),
                "applied": one("SELECT COUNT(*) FROM applications WHERE status = 'applied' "
                               "AND archived_at IS NULL"),
            }
            newest = [dict(r) for r in con.execute(
                "SELECT job_id, title, company, location, match_score FROM v_new_matches "
                "WHERE match_score >= ? ORDER BY discovered_at DESC, match_score DESC LIMIT 5",
                (STRONG_MATCH,))]
        finally:
            con.close()
        return render("today", "today", title="Today", counts=counts,
                      step=next_step(counts), newest=newest, strong_at=STRONG_MATCH,
                      day=date.today().strftime("%A, %B %d").replace(" 0", " "))
